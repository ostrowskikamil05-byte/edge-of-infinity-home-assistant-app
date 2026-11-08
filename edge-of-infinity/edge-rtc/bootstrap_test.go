package main

import (
	"github.com/bluenviron/gortsplib/v5"
	"testing"
)

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
		t.Fatal("cached bootstrap was closed by the 16-packet live limit")
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
