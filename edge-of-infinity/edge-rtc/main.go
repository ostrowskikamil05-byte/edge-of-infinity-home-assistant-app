// EdgeRTC/1: encoded frames over WebSocket, decoded with WebCodecs. No WebRTC or MSE.
package main

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"flag"
	"log"
	"net"
	"net/http"
	"os"
	"os/signal"
	"regexp"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/gorilla/websocket"
)

const version = "0.10.46"

var validID = regexp.MustCompile(`^[A-Za-z0-9_.-]+$`)

type sourceConfig struct {
	ID         string   `json:"id"`
	URL        string   `json:"url"`
	Paths      []string `json:"paths"`
	Microphone bool     `json:"microphone"`
}
type config struct {
	RTSP    string         `json:"rtsp"`
	HTTP    string         `json:"http"`
	Token   string         `json:"token"`
	Sources []sourceConfig `json:"sources"`
}

func readConfig(path string) (config, error) {
	var c config
	b, e := os.ReadFile(path)
	if e != nil {
		return c, e
	}
	if json.Unmarshal(b, &c) != nil || len(c.Token) < 32 {
		return c, errors.New("invalid private config")
	}
	ids := map[string]bool{}
	paths := map[string]bool{}
	urls := map[string]bool{}
	for _, s := range c.Sources {
		u, e := base.ParseURL(s.URL)
		if !validID.MatchString(s.ID) || ids[s.ID] || urls[s.URL] || e != nil || (u.Scheme != "rtsp" && u.Scheme != "rtsps") {
			return c, errors.New("duplicate or invalid source")
		}
		ids[s.ID] = true
		urls[s.URL] = true
		for _, p := range s.Paths {
			if !validID.MatchString(p) || paths[p] {
				return c, errors.New("duplicate or invalid path")
			}
			paths[p] = true
		}
	}
	return c, nil
}

type app struct {
	cfg      config
	mu       sync.RWMutex
	sources  map[string]*source
	paths    map[string]*source
	rtsp     *gortsplib.Server
	server   *http.Server
	listener net.Listener
	stop     chan struct{}
	closed   bool
}

func newApp(c config) (*app, error) {
	a := &app{cfg: c, sources: map[string]*source{}, paths: map[string]*source{}, stop: make(chan struct{})}
	a.rtsp = &gortsplib.Server{RTSPAddress: c.RTSP, Handler: a, WriteQueueSize: 256}
	if e := a.rtsp.Start(); e != nil {
		return nil, e
	}
	a.reload(c)
	a.server = &http.Server{Handler: a, ReadHeaderTimeout: 5 * time.Second, ReadTimeout: 15 * time.Second, WriteTimeout: 15 * time.Second, IdleTimeout: 30 * time.Second, MaxHeaderBytes: 16384}
	ln, e := net.Listen("tcp", c.HTTP)
	if e != nil {
		a.close()
		return nil, e
	}
	a.listener = ln
	go a.server.Serve(ln)
	return a, nil
}
func (a *app) reload(c config) {
	// Removal/changes are closed and joined BEFORE their replacements are started.
	a.mu.Lock()
	if a.closed {
		a.mu.Unlock()
		return
	}
	old := []*source{}
	for id, s := range a.sources {
		same := false
		for _, sc := range c.Sources {
			if sc.ID == id && sc.URL == s.cfg.URL && sc.Microphone == s.cfg.Microphone {
				same = true
				break
			}
		}
		if !same {
			old = append(old, s)
			delete(a.sources, id)
		}
	}
	a.paths = map[string]*source{}
	a.mu.Unlock()
	for _, s := range old {
		s.stop()
	}
	a.mu.Lock()
	defer a.mu.Unlock()
	if a.closed {
		return
	}
	for _, sc := range c.Sources {
		s := a.sources[sc.ID]
		if s == nil {
			s = newSource(sc, a.rtsp)
			a.sources[sc.ID] = s
			go s.run()
		}
		for _, p := range sc.Paths {
			a.paths[p] = s
		}
	}
}
func (a *app) watch(path string) {
	ticker := time.NewTicker(time.Second)
	defer ticker.Stop()
	var last string
	for {
		select {
		case <-a.stop:
			return
		case <-ticker.C:
			b, e := os.ReadFile(path)
			if e != nil || string(b) == last {
				continue
			}
			c, e := readConfig(path)
			if e != nil || c.RTSP != a.cfg.RTSP || c.HTTP != a.cfg.HTTP || c.Token != a.cfg.Token {
				continue
			}
			a.reload(c)
			last = string(b)
		}
	}
}
func (a *app) lookup(path string) *source {
	a.mu.RLock()
	defer a.mu.RUnlock()
	return a.paths[strings.Trim(path, "/")]
}
func reply(w http.ResponseWriter, status int, data any) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)
	json.NewEncoder(w).Encode(data)
}
func (a *app) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	if r.URL.Path == "/health" {
		reply(w, 200, map[string]any{"engine": "edge_rtc", "protocol": "edgertc/1", "version": version})
		return
	}
	if subtle.ConstantTimeCompare([]byte(r.Header.Get("Authorization")), []byte("Bearer "+a.cfg.Token)) != 1 {
		reply(w, 401, map[string]string{"error": "authentication_required"})
		return
	}
	switch {
	case r.URL.Path == "/api/status" && r.Method == "GET":
		out := []any{}
		a.mu.RLock()
		for _, s := range a.sources {
			out = append(out, s.status())
		}
		a.mu.RUnlock()
		reply(w, 200, map[string]any{"engine": "edge_rtc", "protocol": "edgertc/1", "sources": out, "policy": "one_upstream_per_camera"})
	case strings.HasPrefix(r.URL.Path, "/api/stream/") && r.Method == "GET":
		a.stream(w, r, strings.TrimPrefix(r.URL.Path, "/api/stream/"))
	default:
		reply(w, 404, map[string]string{"error": "not_found"})
	}
}

var upgrader = websocket.Upgrader{ReadBufferSize: 1024, WriteBufferSize: 16 * 1024, CheckOrigin: func(r *http.Request) bool { return r.Header.Get("Origin") == "" }}

func (a *app) stream(w http.ResponseWriter, r *http.Request, path string) {
	s := a.lookup(path)
	if s == nil {
		reply(w, 404, map[string]string{"error": "unknown_camera"})
		return
	}
	ws, e := upgrader.Upgrade(w, r, nil)
	if e != nil {
		return
	}
	defer ws.Close()
	v, hello, cache, e := s.attach(r.URL.Query().Get("microphone") == "1")
	if e != nil {
		ws.WriteJSON(map[string]string{"type": "error", "code": e.Error()})
		return
	}
	defer s.detach(v)
	ws.SetReadLimit(1024)
	ws.SetReadDeadline(time.Now().Add(45 * time.Second))
	if v.microphone {
		ws.SetReadDeadline(time.Now().Add(10 * time.Second))
	}
	ws.SetPongHandler(func(string) error {
		if v.microphone {
			return nil
		}
		return ws.SetReadDeadline(time.Now().Add(45 * time.Second))
	})
	ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
	if ws.WriteJSON(hello) != nil {
		return
	}
	go func() {
		defer v.close()
		window := time.Now()
		packets := 0
		for {
			kind, p, e := ws.ReadMessage()
			if e != nil {
				return
			}
			if kind != websocket.BinaryMessage || !v.microphone || len(p) != 321 || p[0] != 3 {
				return
			}
			if time.Since(window) >= time.Second {
				window = time.Now()
				packets = 0
			}
			packets++
			ws.SetReadDeadline(time.Now().Add(5 * time.Second))
			if packets > 100 || s.writePCM(v, p[1:]) != nil {
				return
			}
		}
	}()
	for _, p := range cache {
		ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
		if ws.WriteMessage(websocket.BinaryMessage, p) != nil {
			return
		}
	}
	ticker := time.NewTicker(15 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-a.stop:
			return
		case <-v.done:
			return
		case <-ticker.C:
			ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
			if ws.WriteMessage(websocket.PingMessage, nil) != nil {
				return
			}
		case p := <-v.queue:
			ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
			if ws.WriteMessage(websocket.BinaryMessage, p) != nil {
				return
			}
		}
	}
}
func (a *app) close() {
	close(a.stop)
	a.mu.Lock()
	a.closed = true
	sources := a.sources
	a.sources = map[string]*source{}
	a.paths = map[string]*source{}
	a.mu.Unlock()
	for _, s := range sources {
		s.stop()
	}
	if a.server != nil {
		ctx, c := context.WithTimeout(context.Background(), 2*time.Second)
		a.server.Shutdown(ctx)
		c()
	}
	a.rtsp.Close()
}
func (a *app) OnDescribe(c *gortsplib.ServerHandlerOnDescribeCtx) (*base.Response, *gortsplib.ServerStream, error) {
	return a.rtspStream(c.Path)
}
func (a *app) OnSetup(c *gortsplib.ServerHandlerOnSetupCtx) (*base.Response, *gortsplib.ServerStream, error) {
	return a.rtspStream(c.Path)
}
func (a *app) rtspStream(path string) (*base.Response, *gortsplib.ServerStream, error) {
	s := a.lookup(path)
	if s == nil {
		return &base.Response{StatusCode: base.StatusNotFound}, nil, nil
	}
	s.mu.RLock()
	stream := s.stream
	s.mu.RUnlock()
	if stream == nil {
		return &base.Response{StatusCode: base.StatusServiceUnavailable}, nil, nil
	}
	return &base.Response{StatusCode: base.StatusOK}, stream, nil
}
func (a *app) OnPlay(*gortsplib.ServerHandlerOnPlayCtx) (*base.Response, error) {
	return &base.Response{StatusCode: base.StatusOK}, nil
}
func main() {
	path := flag.String("config", "/tmp/edge-runtime/edge-rtc.json", "private config")
	flag.Parse()
	c, e := readConfig(*path)
	if e != nil {
		log.Fatal("Edge RTC private configuration is invalid")
	}
	a, e := newApp(c)
	if e != nil {
		log.Fatal("Edge RTC listeners could not start")
	}
	log.Printf("Edge RTC %s started (WebCodecs, one upstream per source)", version)
	go a.watch(*path)
	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGTERM, syscall.SIGINT)
	<-quit
	a.close()
}
