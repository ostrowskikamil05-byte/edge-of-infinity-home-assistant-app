package main

import (
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"log"
	"math/big"
	"net"
	"net/http"
	"net/http/httputil"
	"net/url"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"
)

// Disabled by default. This TLS front door proxies HA, including authenticated
// WebSockets and Ingress, rather than exposing the private camera/core APIs.
type localHTTPSConfig struct {
	Enabled   bool   `json:"enabled"`
	Listen    string `json:"listen"`
	Directory string `json:"directory"`
	Upstream  string `json:"upstream"`
}
type localHTTPS struct {
	mu       sync.Mutex
	cfg      localHTTPSConfig
	server   *http.Server
	listener net.Listener
	cert     *tls.Certificate
	problem  string
	closed   bool
}

var localDNSLabel = regexp.MustCompile(`^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$`)
var localNumericHost = regexp.MustCompile(`^[0-9.]+$`)
var localDNS = regexp.MustCompile(`^[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?$`)

func normalizeLocalHosts(hosts []string) ([]string, error) {
	if len(hosts) < 1 || len(hosts) > 8 {
		return nil, errors.New("local_https_hosts_required")
	}
	result := []string{}
	seen := map[string]bool{}
	for _, host := range hosts {
		host = strings.ToLower(strings.TrimSpace(host))
		if ip := net.ParseIP(host); ip != nil {
			if ip.IsUnspecified() || ip.IsMulticast() {
				return nil, errors.New("local_https_invalid_host")
			}
			host = ip.String()
		} else if !localDNS.MatchString(host) || localNumericHost.MatchString(host) {
			return nil, errors.New("local_https_invalid_host")
		}
		if net.ParseIP(host) == nil {
			for _, label := range strings.Split(host, ".") {
				if !localDNSLabel.MatchString(label) {
					return nil, errors.New("local_https_invalid_host")
				}
			}
		}
		if !seen[host] {
			seen[host] = true
			result = append(result, host)
		}
	}
	return result, nil
}
func serial() *big.Int {
	n, e := rand.Int(rand.Reader, new(big.Int).Lsh(big.NewInt(1), 128))
	if e != nil {
		panic(e)
	}
	return n
}
func privatePEM(key *ecdsa.PrivateKey) []byte {
	raw, _ := x509.MarshalPKCS8PrivateKey(key)
	return pem.EncodeToMemory(&pem.Block{Type: "PRIVATE KEY", Bytes: raw})
}
func atomicPrivate(path string, data []byte, mode os.FileMode) error {
	if st, e := os.Lstat(path); e == nil && st.Mode()&os.ModeSymlink != 0 {
		return errors.New("local_https_symlink")
	}
	f, e := os.CreateTemp(filepath.Dir(path), ".tls-")
	if e != nil {
		return e
	}
	name := f.Name()
	defer os.Remove(name)
	if e = f.Chmod(mode); e == nil {
		_, e = f.Write(data)
	}
	if closeErr := f.Close(); e == nil {
		e = closeErr
	}
	if e != nil {
		return e
	}
	return os.Rename(name, path)
}
func localCertificate(directory string, hosts []string) ([]byte, error) {
	names, e := normalizeLocalHosts(hosts)
	if e != nil {
		return nil, e
	}
	if directory == "" {
		return nil, errors.New("local_https_directory_missing")
	}
	if st, err := os.Lstat(directory); err == nil && st.Mode()&os.ModeSymlink != 0 {
		return nil, errors.New("local_https_symlink")
	}
	if e = os.MkdirAll(directory, 0700); e != nil {
		return nil, e
	}
	caPath, keyPath := filepath.Join(directory, "root-ca.crt"), filepath.Join(directory, "root-ca.key")
	for _, name := range []string{caPath, keyPath} {
		if st, err := os.Lstat(name); err == nil && st.Mode()&os.ModeSymlink != 0 {
			return nil, errors.New("local_https_symlink")
		}
	}
	caBytes, caErr := os.ReadFile(caPath)
	keyBytes, keyErr := os.ReadFile(keyPath)
	var ca *x509.Certificate
	var key *ecdsa.PrivateKey
	if errors.Is(caErr, os.ErrNotExist) && errors.Is(keyErr, os.ErrNotExist) {
		key, e = ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		if e != nil {
			return nil, e
		}
		template := &x509.Certificate{SerialNumber: serial(), Subject: pkix.Name{CommonName: "Edge of Infinity local CA"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().AddDate(5, 0, 0), IsCA: true, BasicConstraintsValid: true, MaxPathLenZero: true, KeyUsage: x509.KeyUsageCertSign | x509.KeyUsageCRLSign}
		raw, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
		if err != nil {
			return nil, err
		}
		caBytes = pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: raw})
		ca, err = x509.ParseCertificate(raw)
		if err != nil {
			return nil, err
		}
		if e = atomicPrivate(keyPath, privatePEM(key), 0600); e != nil {
			return nil, e
		}
		if e = atomicPrivate(caPath, caBytes, 0644); e != nil {
			return nil, e
		}
	} else {
		if caErr != nil || keyErr != nil {
			return nil, errors.New("local_https_ca_incomplete")
		}
		pair, err := tls.X509KeyPair(caBytes, keyBytes)
		if err != nil {
			return nil, errors.New("local_https_ca_invalid")
		}
		ca, e = x509.ParseCertificate(pair.Certificate[0])
		if e != nil {
			return nil, e
		}
		var ok bool
		key, ok = pair.PrivateKey.(*ecdsa.PrivateKey)
		if !ok || !ca.IsCA || time.Until(ca.NotAfter) < 48*time.Hour {
			return nil, errors.New("local_https_ca_invalid")
		}
	}
	leafKey, e := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if e != nil {
		return nil, e
	}
	leaf := &x509.Certificate{SerialNumber: serial(), Subject: pkix.Name{CommonName: "Edge of Infinity local HTTPS"}, NotBefore: time.Now().Add(-time.Hour), NotAfter: time.Now().AddDate(1, 0, 0), KeyUsage: x509.KeyUsageDigitalSignature, ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}, BasicConstraintsValid: true}
	if leaf.NotAfter.After(ca.NotAfter) {
		leaf.NotAfter = ca.NotAfter
	}
	for _, name := range names {
		if ip := net.ParseIP(name); ip != nil {
			leaf.IPAddresses = append(leaf.IPAddresses, ip)
		} else {
			leaf.DNSNames = append(leaf.DNSNames, name)
		}
	}
	raw, e := x509.CreateCertificate(rand.Reader, leaf, ca, &leafKey.PublicKey, key)
	if e != nil {
		return nil, e
	}
	if e = atomicPrivate(filepath.Join(directory, "server.key"), privatePEM(leafKey), 0600); e != nil {
		return nil, e
	}
	chain := append(pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: raw}), caBytes...)
	if e = atomicPrivate(filepath.Join(directory, "server.crt"), chain, 0644); e != nil {
		return nil, e
	}
	return caBytes, nil
}
func loadLocalCertificate(c localHTTPSConfig) (*tls.Certificate, error) {
	if c.Directory == "" {
		return nil, errors.New("local_https_directory_missing")
	}
	cert, e := tls.LoadX509KeyPair(filepath.Join(c.Directory, "server.crt"), filepath.Join(c.Directory, "server.key"))
	if e != nil {
		return nil, errors.New("local_https_certificate_missing_or_invalid")
	}
	cert.Leaf, e = x509.ParseCertificate(cert.Certificate[0])
	if e != nil || time.Now().After(cert.Leaf.NotAfter) || time.Now().Before(cert.Leaf.NotBefore) {
		return nil, errors.New("local_https_certificate_expired")
	}
	return &cert, nil
}
func localProxy(target *url.URL, certificate *tls.Certificate) http.Handler {
	proxy := &httputil.ReverseProxy{FlushInterval: -1}
	proxy.Rewrite = func(p *httputil.ProxyRequest) {
		p.SetURL(target)
		p.Out.Host = p.In.Host
		// Preserve HA's authentication and client-IP checks. HA must explicitly
		// trust this proxy; the setup page provides a narrow configuration block.
		p.SetXForwarded()
	}
	proxy.ModifyResponse = func(r *http.Response) error {
		if location := r.Header.Get("Location"); strings.HasPrefix(location, target.Scheme+"://"+r.Request.Host+"/") {
			r.Header.Set("Location", "https://"+r.Request.Host+strings.TrimPrefix(location, target.Scheme+"://"+r.Request.Host))
		}
		return nil
	}
	proxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, e error) {
		log.Printf("local_https_upstream_error method=%s path=%s type=%T", r.Method, r.URL.Path, e)
		http.Error(w, "Home Assistant is not reachable through the local HTTPS gateway", http.StatusBadGateway)
	}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		host := r.Host
		if parsed, _, e := net.SplitHostPort(host); e == nil {
			host = parsed
		}
		if certificate.Leaf.VerifyHostname(host) != nil {
			http.Error(w, "Host is not covered by the local HTTPS certificate", http.StatusMisdirectedRequest)
			return
		}
		proxy.ServeHTTP(w, r)
	})
}
func (l *localHTTPS) apply(c localHTTPSConfig) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.closed {
		return
	}
	previous := l.cfg
	l.cfg = c
	if !c.Enabled {
		if l.server != nil {
			l.server.Close()
		}
		l.server = nil
		l.listener = nil
		l.problem = ""
		return
	}
	cert, e := loadLocalCertificate(c)
	if e != nil {
		l.problem = e.Error()
		return
	}
	target, e := url.Parse(c.Upstream)
	if e != nil || target.Hostname() == "" || target.User != nil || target.RawQuery != "" || target.Fragment != "" || target.Path != "" && target.Path != "/" || (target.Scheme != "http" && target.Scheme != "https") {
		l.problem = "local_https_invalid_upstream"
		return
	}
	if l.server != nil && previous.Listen == c.Listen && previous.Upstream == c.Upstream && previous.Directory == c.Directory {
		l.cert = cert
		l.problem = ""
		return
	}
	// The user-configured listener is independent of recorder/RTSP/source state.
	rawListener, e := net.Listen("tcp", c.Listen)
	if e != nil {
		l.problem = "local_https_port_unavailable"
		return
	}
	ln := &localTLSListener{Listener: rawListener}
	server := &http.Server{Handler: localProxy(target, cert), TLSConfig: &tls.Config{MinVersion: tls.VersionTLS12, Certificates: []tls.Certificate{*cert}}, ReadHeaderTimeout: 10 * time.Second, IdleTimeout: 60 * time.Second, MaxHeaderBytes: 32768}
	old := l.server
	l.server = server
	l.listener = ln
	l.cert = cert
	l.problem = ""
	go func() {
		if err := server.ServeTLS(ln, "", ""); err != nil && !errors.Is(err, http.ErrServerClosed) {
			log.Printf("local_https_listener_error type=%T", err)
		}
	}()
	if old != nil {
		old.Close()
	}
	log.Printf("local_https_started listener=%s", ln.Addr())
}
func (l *localHTTPS) close() {
	l.mu.Lock()
	defer l.mu.Unlock()
	l.closed = true
	if l.server != nil {
		l.server.Close()
	}
	l.server = nil
}
func (l *localHTTPS) status() map[string]any {
	l.mu.Lock()
	defer l.mu.Unlock()
	result := map[string]any{"enabled": l.cfg.Enabled, "active": l.server != nil, "error": l.problem, "port_mapping_required": true}
	if l.listener != nil {
		result["listen"] = l.listener.Addr().String()
	}
	if cert, e := loadLocalCertificate(l.cfg); e == nil {
		fingerprint := sha256.Sum256(cert.Certificate[len(cert.Certificate)-1])
		result["certificate_ready"] = true
		result["certificate_fingerprint"] = hex.EncodeToString(fingerprint[:])
		result["valid_until"] = cert.Leaf.NotAfter
		result["dns_names"] = cert.Leaf.DNSNames
		ips := []string{}
		for _, ip := range cert.Leaf.IPAddresses {
			ips = append(ips, ip.String())
		}
		result["ip_addresses"] = ips
	}
	return result
}
func (a *app) localCertificate(w http.ResponseWriter, r *http.Request) {
	var request struct {
		Hosts []string `json:"hosts"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 4096))
	decoder.DisallowUnknownFields()
	if decoder.Decode(&request) != nil {
		reply(w, 400, map[string]string{"error": "invalid_certificate_request"})
		return
	}
	// Serialize key/certificate generation with listener reloads and other requests.
	a.local.mu.Lock()
	if a.local.server != nil || a.local.closed {
		a.local.mu.Unlock()
		reply(w, 409, map[string]string{"error": "disable_local_https_before_certificate_change"})
		return
	}
	_, e := localCertificate(a.local.cfg.Directory, request.Hosts)
	a.local.mu.Unlock()
	if e != nil {
		reply(w, 400, map[string]string{"error": fmt.Sprint(e)})
		return
	}
	reply(w, 200, a.local.status())
}

// http.Server.Close does not close hijacked WebSockets. Own their underlying
// sockets too, and remove them immediately when either peer closes.
type localTLSListener struct {
	net.Listener
	connections sync.Map
}
type localTLSConn struct {
	net.Conn
	owner *localTLSListener
}

func (l *localTLSListener) Accept() (net.Conn, error) {
	c, e := l.Listener.Accept()
	if e != nil {
		return nil, e
	}
	wrapped := &localTLSConn{Conn: c, owner: l}
	l.connections.Store(wrapped, true)
	return wrapped, nil
}
func (c *localTLSConn) Close() error { c.owner.connections.Delete(c); return c.Conn.Close() }
func (l *localTLSListener) Close() error {
	e := l.Listener.Close()
	l.connections.Range(func(key, value any) bool { key.(*localTLSConn).Close(); return true })
	return e
}
