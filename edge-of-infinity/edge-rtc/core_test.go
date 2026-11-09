package main

import (
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/h264"
	"net"
	"net/http"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"sync/atomic"
	"syscall"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/gorilla/websocket"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
)

func freeAddress() string {
	ln, _ := net.Listen("tcp", "127.0.0.1:0")
	addr := ln.Addr().String()
	ln.Close()
	return addr
}
func eventually(t *testing.T, f func() bool) {
	t.Helper()
	end := time.Now().Add(8 * time.Second)
	for time.Now().Before(end) {
		if f() {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("condition timed out")
}

type fakeCamera struct {
	server       *gortsplib.Server
	stream       *gortsplib.ServerStream
	direct       *description.Media
	back         *description.Media
	active       atomic.Int32
	maximum      atomic.Int32
	mic          atomic.Uint64
	pli          atomic.Uint64
	idrRequested atomic.Bool
	done         chan struct{}
	onOpen       func()
}

func (c *fakeCamera) OnConnOpen(*gortsplib.ServerHandlerOnConnOpenCtx) {
	if c.onOpen != nil {
		c.onOpen()
	}
	n := c.active.Add(1)
	for old := c.maximum.Load(); n > old; old = c.maximum.Load() {
		if c.maximum.CompareAndSwap(old, n) {
			break
		}
	}
}
func (c *fakeCamera) OnConnClose(*gortsplib.ServerHandlerOnConnCloseCtx) { c.active.Add(-1) }
func (c *fakeCamera) OnDescribe(*gortsplib.ServerHandlerOnDescribeCtx) (*base.Response, *gortsplib.ServerStream, error) {
	return &base.Response{StatusCode: base.StatusOK}, c.stream, nil
}
func (c *fakeCamera) OnSetup(*gortsplib.ServerHandlerOnSetupCtx) (*base.Response, *gortsplib.ServerStream, error) {
	return &base.Response{StatusCode: base.StatusOK}, c.stream, nil
}
func (c *fakeCamera) OnPlay(ctx *gortsplib.ServerHandlerOnPlayCtx) (*base.Response, error) {
	ctx.Session.OnPacketRTCPAny(func(_ *description.Media, p rtcp.Packet) {
		if _, ok := p.(*rtcp.PictureLossIndication); ok {
			c.pli.Add(1)
			c.idrRequested.Store(true)
		}
	})
	ctx.Session.OnPacketRTPAny(func(m *description.Media, _ format.Format, p *rtp.Packet) {
		if m.IsBackChannel && len(p.Payload) == 160 {
			c.mic.Add(1)
		}
	})
	return &base.Response{StatusCode: base.StatusOK}, nil
}
func newFake(t *testing.T, onOpen ...func()) *fakeCamera {
	t.Helper()
	sps, _ := hex.DecodeString("6742c00ada0a37e4c044000003000400000300503c489a80")
	pps, _ := hex.DecodeString("68ce0fc8")
	c := &fakeCamera{done: make(chan struct{})}
	if len(onOpen) > 0 {
		c.onOpen = onOpen[0]
	}
	c.direct = &description.Media{Type: description.MediaTypeVideo, Formats: []format.Format{&format.H264{PayloadTyp: 96, PacketizationMode: 1, SPS: sps, PPS: pps}}}
	c.back = &description.Media{Type: description.MediaTypeAudio, IsBackChannel: true, Formats: []format.Format{&format.G711{PayloadTyp: 8, SampleRate: 8000, ChannelCount: 1}}}
	c.server = &gortsplib.Server{RTSPAddress: freeAddress(), Handler: c}
	if e := c.server.Start(); e != nil {
		t.Fatal(e)
	}
	c.stream = &gortsplib.ServerStream{Server: c.server, Desc: &description.Session{Medias: []*description.Media{c.direct, c.back}}}
	if e := c.stream.Initialize(); e != nil {
		t.Fatal(e)
	}
	go func() {
		tick := time.NewTicker(33 * time.Millisecond)
		defer tick.Stop()
		var seq uint16
		for {
			select {
			case <-c.done:
				return
			case <-tick.C:
				seq++
				c.stream.WritePacketRTP(c.direct, &rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 96, Marker: true, SequenceNumber: seq, Timestamp: uint32(seq) * 3000, SSRC: 123}, Payload: []byte{0x65, 0x88, 0x84, 0x21}})
			}
		}
	}()
	t.Cleanup(func() { close(c.done); c.server.Close(); c.stream.Close() })
	return c
}
func TestSharedSourceAndMicrophone(t *testing.T) {
	c := newFake(t)
	cfg := config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("x", 40), Sources: []sourceConfig{{ID: "front", URL: "rtsp://" + c.server.RTSPAddress + "/camera", Paths: []string{"front_main", "front_sub"}, Microphone: true}}}
	a, e := newApp(cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer a.close()
	source := a.lookup("front_main")
	eventually(t, func() bool { return source.connected.Load() == 1 && source.packets.Load() > 0 })
	headers := http.Header{"Authorization": []string{"Bearer " + cfg.Token}}
	// Two paths are aliases of the same camera registry entry, not separate camera clients.
	if a.lookup("front_sub") != source {
		t.Fatal("path duplication")
	}
	for i := 0; i < 3; i++ {
		u, _ := base.ParseURL("rtsp://" + cfg.RTSP + "/front_main")
		protocol := gortsplib.ProtocolTCP
		reader := &gortsplib.Client{Scheme: u.Scheme, Host: u.Host, Protocol: &protocol}
		if e = reader.Start(); e != nil {
			t.Fatal(e)
		}
		defer reader.Close()
		desc, _, e := reader.Describe(u)
		if e != nil {
			t.Fatal(e)
		}
		if e = reader.SetupAll(desc.BaseURL, desc.Medias); e != nil {
			t.Fatal(e)
		}
		if _, e = reader.Play(nil); e != nil {
			t.Fatal(e)
		}
	}
	for i := 0; i < 4; i++ {
		suffix := ""
		if i == 3 {
			suffix = "?microphone=1"
		}
		ws, _, e := websocket.DefaultDialer.Dial("ws://"+cfg.HTTP+"/api/stream/front_main"+suffix, headers)
		if e != nil {
			t.Fatal(e)
		}
		defer ws.Close()
		var hello map[string]any
		if e = ws.ReadJSON(&hello); e != nil {
			t.Fatal(e)
		}
		if hello["protocol"] != "edgertc/1" {
			t.Fatal(hello)
		}
		ws.SetReadDeadline(time.Now().Add(3 * time.Second))
		kind, p, e := ws.ReadMessage()
		if e != nil || kind != websocket.BinaryMessage || p[0] != 1 || p[1] != 1 || len(p) < 20 {
			t.Fatalf("invalid encoded frame: %v %d", e, len(p))
		}
		if i == 3 {
			pcm := make([]byte, 321)
			pcm[0] = 3
			for j := 1; j < 321; j += 2 {
				binary.LittleEndian.PutUint16(pcm[j:j+2], 1000)
			}
			if e = ws.WriteMessage(websocket.BinaryMessage, pcm); e != nil {
				t.Fatal(e)
			}
		}
	}
	eventually(t, func() bool { return c.mic.Load() > 0 })
	if c.maximum.Load() != 1 || c.active.Load() != 1 || source.attempts.Load() != 1 {
		t.Fatalf("extra camera connections: max %d active %d attempts %d", c.maximum.Load(), c.active.Load(), source.attempts.Load())
	}
	response, e := http.Get("http://" + cfg.HTTP + "/api/status")
	if e != nil {
		t.Fatal(e)
	}
	response.Body.Close()
	if response.StatusCode != 401 {
		t.Fatal("private API exposed")
	}
	t.Log("4 EdgeRTC viewers, 3 RTSP readers and microphone share ONE real RTSP camera connection")
}
func TestPrivateMetadataAndReload(t *testing.T) {
	var previous atomic.Pointer[source]
	var overlap atomic.Bool
	c := newFake(t, func() {
		if old := previous.Load(); old != nil {
			select {
			case <-old.stopped:
			default:
				overlap.Store(true)
			}
		}
	})
	cfg := config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("z", 40), Sources: []sourceConfig{{ID: "front", URL: "rtsp://" + c.server.RTSPAddress + "/camera", Paths: []string{"front_main"}}}}
	a, e := newApp(cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer a.close()
	eventually(t, func() bool { return a.lookup("front_main").connected.Load() == 1 })
	first := a.lookup("front_main")
	previous.Store(first)
	a.reload(cfg)
	if a.lookup("front_main") != first {
		t.Fatal("unchanged source restarted")
	}
	cfg.Sources[0].URL += "?new_settings=1"
	a.reload(cfg)
	eventually(t, func() bool { return a.lookup("front_main").connected.Load() == 1 })
	if overlap.Load() {
		t.Fatal("replacement connected before the previous source and its client closed")
	}
	// Remote close callbacks can lag behind TCP close; source ownership must not overlap.
	eventually(t, func() bool { return c.active.Load() == 1 })
	b, _ := json.Marshal(a.lookup("front_main").status())
	if strings.Contains(string(b), "rtsp://") {
		t.Fatal("source URL leaked")
	}
	fmt.Println("PASS serial source replacement and private status")
}

// TestBrowserFixture runs only when explicitly requested by the disposable browser suite.
func TestBrowserFixture(t *testing.T) {
	file := os.Getenv("EDGE_RTC_TEST_VIDEO")
	if file == "" {
		t.Skip("browser fixture not requested")
	}
	raw, e := os.ReadFile(file)
	if e != nil {
		t.Fatal(e)
	}
	var nalus h264.AnnexB
	if e = nalus.Unmarshal(raw); e != nil {
		t.Fatal(e)
	}
	var frames [][][]byte
	var current [][]byte
	var vps, sps, pps []byte
	hevc := os.Getenv("EDGE_RTC_TEST_CODEC") == "h265"
	fps, _ := strconv.Atoi(os.Getenv("EDGE_RTC_TEST_FPS"))
	if fps < 1 || fps > 60 {
		fps = 10
	}
	for _, n := range nalus {
		typ := n[0] & 31
		aud := byte(9)
		if hevc {
			typ = (n[0] >> 1) & 63
			aud = 35
		}
		if (!hevc && typ == 7) || (hevc && typ == 33) {
			sps = n
		}
		if (!hevc && typ == 8) || (hevc && typ == 34) {
			pps = n
		}
		if hevc && typ == 32 {
			vps = n
		}
		if typ == aud && len(current) > 0 {
			frames = append(frames, current)
			current = nil
		}
		current = append(current, n)
	}
	if len(current) > 0 {
		frames = append(frames, current)
	}
	c := &fakeCamera{done: make(chan struct{})}
	var videoFormat format.Format
	var encoder interface {
		Encode([][]byte) ([]*rtp.Packet, error)
	}
	if hevc {
		f := &format.H265{PayloadTyp: 96, VPS: vps, SPS: sps, PPS: pps}
		videoFormat = f
		encoder, e = f.CreateEncoder()
	} else {
		f := &format.H264{PayloadTyp: 96, PacketizationMode: 1, SPS: sps, PPS: pps}
		videoFormat = f
		encoder, e = f.CreateEncoder()
	}
	if e != nil {
		t.Fatal(e)
	}
	c.direct = &description.Media{Type: description.MediaTypeVideo, Formats: []format.Format{videoFormat}}
	if os.Getenv("EDGE_RTC_TEST_NO_SDP_PARAMS") == "1" {
		if hevc {
			videoFormat.(*format.H265).SafeSetParams(nil, nil, nil)
		} else {
			videoFormat.(*format.H264).SafeSetParams(nil, nil)
		}
	}
	audio := &description.Media{Type: description.MediaTypeAudio, Formats: []format.Format{&format.G711{PayloadTyp: 8, SampleRate: 8000, ChannelCount: 1}}}
	c.back = &description.Media{Type: description.MediaTypeAudio, IsBackChannel: true, Formats: []format.Format{&format.G711{PayloadTyp: 8, SampleRate: 8000, ChannelCount: 1}}}
	c.server = &gortsplib.Server{RTSPAddress: freeAddress(), Handler: c}
	if e = c.server.Start(); e != nil {
		t.Fatal(e)
	}
	defer c.server.Close()
	c.stream = &gortsplib.ServerStream{Server: c.server, Desc: &description.Session{Medias: []*description.Media{c.direct, audio, c.back}}}
	if e = c.stream.Initialize(); e != nil {
		t.Fatal(e)
	}
	defer c.stream.Close()
	go func() {
		ticker := time.NewTicker(time.Second / time.Duration(fps))
		defer ticker.Stop()
		index := 0
		frameIndex := 0
		var sequence uint16
		silence := make([]byte, 8000/fps)
		for i := range silence {
			silence[i] = 0xD5
		}
		for {
			select {
			case <-c.done:
				return
			case <-ticker.C:
				if os.Getenv("EDGE_RTC_TEST_RESPOND_PLI") == "1" && c.idrRequested.Swap(false) {
					frameIndex = 0
				}
				packets, e := encoder.Encode(frames[frameIndex%len(frames)])
				if e == nil {
					for _, p := range packets {
						p.Timestamp = uint32(uint64(index) * 90000 / uint64(fps))
						c.stream.WritePacketRTP(c.direct, p)
					}
				}
				sequence++
				c.stream.WritePacketRTP(audio, &rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 8, SSRC: 456, SequenceNumber: sequence, Timestamp: uint32(index) * uint32(len(silence))}, Payload: silence})
				index++
				frameIndex++
			}
		}
	}()
	defer close(c.done)
	cfg := config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("fixture-private-", 4), Sources: []sourceConfig{{ID: "front", URL: "rtsp://" + c.server.RTSPAddress + "/camera", Paths: []string{"front_main", "front_sub"}, Microphone: true}}}
	a, e := newApp(cfg)
	if e != nil {
		t.Fatal(e)
	}
	defer a.close()
	eventually(t, func() bool {
		return a.lookup("front_main").connected.Load() == 1 && a.lookup("front_main").packets.Load() > 20
	})
	info, _ := json.Marshal(map[string]any{"api": "http://" + cfg.HTTP, "token": cfg.Token, "rtsp": "rtsp://" + cfg.RTSP + "/front_main"})
	if e = os.WriteFile(os.Getenv("EDGE_RTC_TEST_INFO"), info, 0600); e != nil {
		t.Fatal(e)
	}
	stop := make(chan os.Signal, 1)
	signal.Notify(stop, syscall.SIGTERM, syscall.SIGINT)
	defer signal.Stop(stop)
	<-stop
	if c.maximum.Load() != 1 {
		t.Fatalf("camera connection maximum: %d", c.maximum.Load())
	}
	t.Logf("physical camera max=1, microphone packets=%d", c.mic.Load())
}
