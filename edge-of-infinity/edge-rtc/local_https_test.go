package main

import (
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"github.com/gorilla/websocket"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLocalHTTPSCertificateAndAuthenticatedProxy(t *testing.T) {
	directory := t.TempDir()
	ca, e := localCertificate(directory, []string{"127.0.0.1", "localhost"})
	if e != nil {
		t.Fatal(e)
	}
	original := sha256.Sum256(ca)
	for _, file := range []string{"root-ca.key", "server.key"} {
		st, e := os.Stat(filepath.Join(directory, file))
		if e != nil || st.Mode().Perm() != 0600 {
			t.Fatalf("private permissions %s: %v", file, e)
		}
	}
	for _, hosts := range [][]string{{"https://127.0.0.1"}, {"0.0.0.0"}, {"bad/../../name"}, {"*.local"}} {
		if _, e = localCertificate(directory, hosts); e == nil {
			t.Fatal("invalid host accepted", hosts)
		}
	}
	if _, e = localCertificate(directory, []string{"127.0.0.1", "localhost"}); e != nil {
		t.Fatal(e)
	}
	again, _ := os.ReadFile(filepath.Join(directory, "root-ca.crt"))
	if sha256.Sum256(again) != original {
		t.Fatal("regeneration replaced trusted CA")
	}
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer disposable-test" {
			http.Error(w, "unauthorized", 401)
			return
		}
		if r.Header.Get("X-Forwarded-Proto") != "https" || r.Header.Get("X-Forwarded-For") != "127.0.0.1" {
			t.Error("client/scheme not preserved", r.Header)
		}
		if r.URL.Path == "/socket" {
			u := websocket.Upgrader{CheckOrigin: func(r *http.Request) bool { return r.Header.Get("Origin") == "https://"+r.Host }}
			ws, e := u.Upgrade(w, r, nil)
			if e != nil {
				return
			}
			defer ws.Close()
			for {
				kind, data, e := ws.ReadMessage()
				if e != nil {
					return
				}
				if ws.WriteMessage(kind, data) != nil {
					return
				}
			}
		}
		// Like HA JSON endpoints, consume the request before replying. Go's
		// HTTP/1 server otherwise drains/closes a large body on the first write.
		payload, err := io.ReadAll(r.Body)
		if err != nil {
			t.Error(err)
			http.Error(w, "bad body", 400)
			return
		}
		w.Write(payload)
	}))
	defer backend.Close()
	cfg := localHTTPSConfig{Enabled: true, Listen: "127.0.0.1:0", Directory: directory, Upstream: backend.URL}
	gateway := &localHTTPS{}
	gateway.apply(cfg)
	defer gateway.close()
	if gateway.server == nil {
		t.Fatal(gateway.status())
	}
	address := gateway.listener.Addr().String()
	roots := x509.NewCertPool()
	roots.AppendCertsFromPEM(ca)
	client := &http.Client{Transport: &http.Transport{TLSClientConfig: &tls.Config{RootCAs: roots}}, Timeout: 3 * time.Second}
	defer client.CloseIdleConnections()
	response, e := client.Get("https://" + address + "/")
	if e != nil {
		t.Fatal(e)
	}
	response.Body.Close()
	if response.StatusCode != 401 {
		t.Fatal("bypassed HA authentication")
	}
	body := strings.Repeat("original-media-bytes", 30000)
	request, _ := http.NewRequest("POST", "https://"+address+"/api/data", strings.NewReader(body))
	request.Header.Set("Authorization", "Bearer disposable-test")
	request.Header.Set("X-Forwarded-For", "192.0.2.99")
	response, e = client.Do(request)
	if e != nil {
		t.Fatal(e)
	}
	got, _ := io.ReadAll(response.Body)
	response.Body.Close()
	if string(got) != body {
		t.Fatal("changed media bytes")
	}
	request, _ = http.NewRequest("GET", "https://"+address+"/api/status", nil)
	response, e = client.Do(request)
	if e != nil {
		t.Fatal(e)
	}
	response.Body.Close()
	if response.StatusCode != 401 {
		t.Fatal("exposed private core API")
	}
	headers := http.Header{"Authorization": []string{"Bearer disposable-test"}, "Origin": []string{"https://" + address}}
	dialer := websocket.Dialer{TLSClientConfig: &tls.Config{RootCAs: roots}, HandshakeTimeout: 3 * time.Second}
	ws, _, e := dialer.Dial("wss://"+address+"/socket", headers)
	if e != nil {
		t.Fatal(e)
	}
	defer ws.Close()
	if e = ws.WriteMessage(websocket.BinaryMessage, []byte(body)); e != nil {
		t.Fatal(e)
	}
	_, data, e := ws.ReadMessage()
	if e != nil || string(data) != body {
		t.Fatal("TLS WebSocket changed media", e)
	}
	listener := gateway.listener
	gateway.apply(cfg)
	if gateway.listener != listener {
		t.Fatal("ordinary reload restarted TLS")
	}
	cfg.Enabled = false
	gateway.apply(cfg)
	ws.SetReadDeadline(time.Now().Add(time.Second))
	if _, _, e = ws.ReadMessage(); e == nil {
		t.Fatal("disabled gateway retained WebSocket")
	}
}
func TestLocalHTTPSFailureDoesNotStopCore(t *testing.T) {
	app, e := newApp(config{HTTP: freeAddress(), RTSP: freeAddress(), Token: strings.Repeat("x", 48), LocalHTTPS: localHTTPSConfig{Enabled: true, Listen: "127.0.0.1:0", Directory: t.TempDir(), Upstream: "http://127.0.0.1:8123"}})
	if e != nil {
		t.Fatal(e)
	}
	defer app.close()
	request, _ := http.NewRequest("GET", "http://"+app.listener.Addr().String()+"/api/status", nil)
	request.Header.Set("Authorization", "Bearer "+app.cfg.Token)
	response, e := http.DefaultClient.Do(request)
	if e != nil {
		t.Fatal(e)
	}
	response.Body.Close()
	if response.StatusCode != 200 || app.local.status()["active"] != false {
		t.Fatal("optional TLS failure affected core")
	}
	if app.local.status()["error"] == "" {
		t.Fatal("certificate error hidden")
	}
}
func TestLocalHTTPSRejectsUntrustedCertificate(t *testing.T) {
	dir := t.TempDir()
	if _, e := localCertificate(dir, []string{"127.0.0.1"}); e != nil {
		t.Fatal(e)
	}
	backend := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("ok")) }))
	defer backend.Close()
	gateway := &localHTTPS{}
	gateway.apply(localHTTPSConfig{Enabled: true, Listen: "127.0.0.1:0", Directory: dir, Upstream: backend.URL})
	defer gateway.close()
	client := &http.Client{Timeout: time.Second}
	_, e := client.Get("https://" + gateway.listener.Addr().String())
	if e == nil {
		t.Fatal("untrusted CA accepted")
	}
	cert, _ := loadLocalCertificate(localHTTPSConfig{Directory: dir})
	if cert.Leaf.VerifyHostname("192.0.2.50") == nil {
		t.Fatal("wrong address accepted")
	}
}
