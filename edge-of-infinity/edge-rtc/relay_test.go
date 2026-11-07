package main

import (
	"bytes"
	"strings"
	"sync"
	"sync/atomic"
	"testing"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/gortsplib/v5/pkg/liberrors"
	"github.com/pion/rtp"
)

func relayClient(t *testing.T, a *app, agent string, onPacket func(*rtp.Packet)) *gortsplib.Client {
	t.Helper()
	u, _ := base.ParseURL("rtsp://" + a.cfg.RTSP + "/front_main")
	protocol := gortsplib.ProtocolTCP
	client := &gortsplib.Client{Scheme: u.Scheme, Host: u.Host, Protocol: &protocol, UserAgent: agent}
	if err := client.Start(); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(client.Close)
	desc, _, err := client.Describe(u)
	if err != nil {
		t.Fatal(err)
	}
	if err = client.SetupAll(desc.BaseURL, desc.Medias); err != nil {
		t.Fatal(err)
	}
	client.OnPacketRTPAny(func(_ *description.Media, _ format.Format, p *rtp.Packet) { onPacket(p) })
	if _, err = client.Play(nil); err != nil {
		t.Fatal(err)
	}
	return client
}

func TestRelayLargeBurstPreservesEveryPacket(t *testing.T) {
	camera := newFake(t)
	cfg := config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("b", 40), Sources: []sourceConfig{{ID: "front", URL: "rtsp://" + camera.server.RTSPAddress + "/camera", Paths: []string{"front_main"}}}}
	app, err := newApp(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer app.close()
	source := app.lookup("front_main")
	eventually(t, func() bool { return source.connected.Load() == 1 })
	const packets = 1536 // exceeds the old 256-packet queue by six times
	var mu sync.Mutex
	received := map[uint16][]byte{}
	relayClient(t, app, "EdgeRecorder/test", func(p *rtp.Packet) {
		if len(p.Payload) == 1000 {
			mu.Lock()
			received[p.SequenceNumber] = append([]byte{}, p.Payload...)
			mu.Unlock()
		}
	})
	var stream *gortsplib.ServerStream
	source.mu.RLock()
	stream = source.stream
	media := stream.Desc.Medias[0]
	source.mu.RUnlock()
	for i := 0; i < packets; i++ {
		payload := bytes.Repeat([]byte{byte(i % 251)}, 1000)
		payload[0] = 0x65
		if err = stream.WritePacketRTP(media, &rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 96, SequenceNumber: uint16(10000 + i), Timestamp: 900000, SSRC: 123, Marker: i == packets-1}, Payload: payload}); err != nil {
			t.Fatal(err)
		}
	}
	eventually(t, func() bool { mu.Lock(); defer mu.Unlock(); return len(received) == packets })
	mu.Lock()
	defer mu.Unlock()
	for i := 0; i < packets; i++ {
		expected := bytes.Repeat([]byte{byte(i % 251)}, 1000)
		expected[0] = 0x65
		if !bytes.Equal(received[uint16(10000+i)], expected) {
			t.Fatal("RTP payload changed")
		}
	}
	if source.relayOverflows.Load() != 0 || source.attempts.Load() != 1 || camera.maximum.Load() != 1 {
		t.Fatal("burst damaged a reader or restarted camera")
	}
}

func TestSlowAnalysisReaderIsolatedFromRecorderAndCamera(t *testing.T) {
	camera := newFake(t)
	cfg := config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("s", 40), Sources: []sourceConfig{{ID: "front", URL: "rtsp://" + camera.server.RTSPAddress + "/camera", Paths: []string{"front_main"}}}}
	app, err := newApp(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer app.close()
	source := app.lookup("front_main")
	eventually(t, func() bool { return source.connected.Load() == 1 })
	var healthy atomic.Int32
	relayClient(t, app, "EdgeRecorder/test", func(*rtp.Packet) { healthy.Add(1) })
	analysis := relayClient(t, app, "EdgeAI/test", func(*rtp.Packet) {})
	eventually(t, func() bool { return healthy.Load() > 2 })
	var session *gortsplib.ServerSession
	app.readers.Range(func(key, value any) bool {
		if value.(*relayReader).role == "analysis" {
			session = key.(*gortsplib.ServerSession)
			return false
		}
		return true
	})
	if session == nil {
		t.Fatal("analysis reader not classified")
	}
	before := healthy.Load()
	// Exercise the exact callback gortsplib emits when that reader's bounded
	// writer fills. Repeated errors must close/count/log the consumer once.
	for i := 0; i < 100; i++ {
		app.OnStreamWriteError(&gortsplib.ServerHandlerOnStreamWriteErrorCtx{Session: session, Error: liberrors.ErrServerWriteQueueFull{}})
	}
	eventually(t, func() bool { _, exists := app.readers.Load(session); return !exists && healthy.Load() > before+2 })
	analysis.Close()
	if source.relayFailures.Load() != 1 || source.relayOverflows.Load() != 1 || source.relayLastRole.Load() != "analysis" {
		t.Fatal("incorrect isolation counters")
	}
	if source.connected.Load() != 1 || source.attempts.Load() != 1 || camera.maximum.Load() != 1 {
		t.Fatal("slow local reader restarted the physical camera")
	}
}
