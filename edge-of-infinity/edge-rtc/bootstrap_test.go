package main

import (
	"github.com/bluenviron/gortsplib/v5"
	"github.com/gorilla/websocket"
	"net/http"
	"strings"
	"testing"
	"time"
)

func TestViewerWaitsForSourceMetadataOnSameSocket(t *testing.T) {
	a, err := newApp(config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("w", 40)})
	if err != nil {
		t.Fatal(err)
	}
	defer a.close()
	s := cachedSource(t)
	s.video = nil
	a.mu.Lock()
	a.paths["warming_main"] = s
	a.mu.Unlock()
	time.AfterFunc(150*time.Millisecond, func() {
		s.setVideo(map[string]any{"codec": "avc1.640028", "codec_name": "h264"})
		s.broadcast(frame(1, true, 0, []byte{1, 2, 3}), true)
	})
	ws, _, err := websocket.DefaultDialer.Dial("ws://"+a.cfg.HTTP+"/api/stream/warming_main", http.Header{"Authorization": []string{"Bearer " + a.cfg.Token}})
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	ws.SetReadDeadline(time.Now().Add(5 * time.Second))
	var hello map[string]any
	if err = ws.ReadJSON(&hello); err != nil {
		t.Fatal(err)
	}
	if hello["type"] != "hello" || hello["metadata_wait_ms"].(float64) < 100 || hello["metadata_wait_ms"].(float64) > 3000 {
		t.Fatal("metadata race disconnected viewer", hello)
	}
}

func TestKeyframeRequestUsesExistingSessionAndCoalesces(t *testing.T) {
	c := newFake(t)
	a, err := newApp(config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("k", 40), Sources: []sourceConfig{{ID: "front", URL: "rtsp://" + c.server.RTSPAddress + "/camera", Paths: []string{"front_main"}}}})
	if err != nil {
		t.Fatal(err)
	}
	defer a.close()
	s := a.lookup("front_main")
	eventually(t, func() bool { return s.videoSSRC.Load() != 0 })
	if !s.requestKeyframe() {
		t.Fatal("request not queued")
	}
	eventually(t, func() bool { return c.pli.Load() == 1 })
	for i := 0; i < 20; i++ {
		if s.requestKeyframe() {
			t.Fatal("viewer joins flooded camera feedback")
		}
	}
	if c.maximum.Load() != 1 || s.attempts.Load() != 1 {
		t.Fatal("feedback opened another camera session")
	}
}

func cachedSource(t *testing.T) *source {
	s := newSource(sourceConfig{}, nil)
	t.Cleanup(s.cancel)
	s.stream = &gortsplib.ServerStream{}
	s.video = map[string]any{"codec": "avc1.640028"}
	return s
}

func TestLongGOPRemainsReadyForJoiningViewer(t *testing.T) {
	s := cachedSource(t)
	for i := 0; i < 600; i++ {
		s.broadcast(frame(1, i == 0, int64(i)*50000, []byte{1, 2, 3}), i == 0)
	}
	v, hello, cache, err := s.attach(false)
	if err != nil {
		t.Fatal(err)
	}
	defer s.detach(v)
	if len(cache) != 600 || hello["bootstrap_packets"] != 600 {
		t.Fatalf("long GOP lost: %d, %v", len(cache), hello)
	}
	if !s.status()["bootstrap_ready"].(bool) {
		t.Fatal("viewer would wait for the next camera keyframe")
	}
	// Live packets arriving while cached video is sent have a larger bounded
	// startup queue. After it drains, retain the normal low-latency limit.
	for i := 0; i < 100; i++ {
		s.broadcast(frame(1, false, int64(600+i)*50000, []byte{4}), false)
	}
	select {
	case <-v.done:
		t.Fatal("cached bootstrap was closed by the live limit")
	default:
	}
	for len(v.queue) > 0 {
		v.consumed(<-v.queue)
	}
	if v.bootstrap.Load() || v.queueBytes.Load() != 0 {
		t.Fatal("bootstrap budget did not drain")
	}
	for i := 0; i < liveQueuePackets+1; i++ {
		s.broadcast(frame(1, false, int64(i), []byte{4}), false)
	}
	select {
	case <-v.done:
	default:
		t.Fatal("steady-state slow-viewer protection was lost")
	}
}

func TestLiveAudioVideoJitterDoesNotDisconnectViewer(t *testing.T) {
	s := cachedSource(t)
	v, _, _, err := s.attach(false)
	if err != nil {
		t.Fatal(err)
	}
	defer s.detach(v)
	// One second at 20 video and 50 audio packets/s exceeds the old limit.
	for i := 0; i < 70; i++ {
		kind := byte(2)
		if i%7 < 2 {
			kind = 1
		}
		s.broadcast(frame(kind, false, int64(i)*14000, []byte{1}), false)
	}
	select {
	case <-v.done:
		t.Fatal("ordinary mobile jitter disconnected viewer")
	default:
	}
	for len(v.queue) > 0 {
		v.consumed(<-v.queue)
	}
	if v.queueBytes.Load() != 0 {
		t.Fatal("drained queue retained byte budget")
	}
}

func TestRefreshBootstrapWaitsForFreshKeyframeAndDrainsDuplicates(t *testing.T) {
	s := cachedSource(t)
	s.broadcast(frame(1, true, 100, []byte{1}), true)
	v, hello, cache, err := s.attach(false)
	if err != nil {
		t.Fatal(err)
	}
	defer s.detach(v)
	result := make(chan [][]byte, 1)
	go func() { result <- s.refreshBootstrap(v, hello, cache, time.Second) }()
	s.broadcast(frame(1, false, 200, []byte{2}), false)
	s.broadcast(frame(1, true, 300, []byte{3}), true)
	fresh := <-result
	if len(fresh) != 1 || fresh[0][10] != 3 || hello["bootstrap_refreshed"] != true || hello["bootstrap_timestamp_us"] != int64(300) {
		t.Fatalf("stale bootstrap retained: %v, %v", fresh, hello)
	}
	if len(v.queue) != 0 || v.queueBytes.Load() != 0 || !v.bootstrap.Load() {
		t.Fatal("duplicate live packets were retained")
	}
	s.broadcast(frame(1, false, 400, []byte{4}), false)
	if len(v.queue) != 1 {
		t.Fatal("new live packets were discarded")
	}
}

func TestRefreshBootstrapTimeoutKeepsOriginalAndQueuedFrames(t *testing.T) {
	s := cachedSource(t)
	s.broadcast(frame(1, true, 100, []byte{1}), true)
	v, hello, cache, err := s.attach(false)
	if err != nil {
		t.Fatal(err)
	}
	defer s.detach(v)
	s.broadcast(frame(1, false, 200, []byte{2}), false)
	fresh := s.refreshBootstrap(v, hello, cache, time.Millisecond)
	if len(fresh) != 1 || fresh[0][10] != 1 || hello["bootstrap_refreshed"] != nil || len(v.queue) != 1 {
		t.Fatal("timeout lost usable cached or live video")
	}
}

func TestGOPAndBootstrapQueuesStayBounded(t *testing.T) {
	s := cachedSource(t)
	for i := 0; i < gopPacketLimit+1; i++ {
		s.broadcast(frame(1, i == 0, int64(i), []byte{1}), i == 0)
	}
	if len(s.gop) != 0 || s.gopResetReason != "packet_limit" {
		t.Fatal("packet cap failed")
	}
	s.broadcast(frame(1, true, 0, []byte{1}), true)
	v, _, _, err := s.attach(false)
	if err != nil {
		t.Fatal(err)
	}
	defer s.detach(v)
	s.broadcast(frame(2, false, 0, make([]byte, viewerQueueByteLimit+1)), false)
	select {
	case <-v.done:
	default:
		t.Fatal("startup byte cap failed")
	}
	if v.queueBytes.Load() != 0 {
		t.Fatal("rejected packet consumed queue budget")
	}
	s.broadcast(frame(1, true, 0, make([]byte, gopByteLimit+1)), true)
	if len(s.gop) != 0 || s.gopBytes != 0 || s.gopResetReason != "byte_limit" {
		t.Fatal("byte cap failed")
	}
}
