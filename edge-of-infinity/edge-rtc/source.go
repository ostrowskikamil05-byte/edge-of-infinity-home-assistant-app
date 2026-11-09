package main

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"fmt"
	"log"
	"math/rand/v2"
	"sync"
	"sync/atomic"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/g711"
	codech264 "github.com/bluenviron/mediacommon/v2/pkg/codecs/h264"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
)

const (
	gopPacketLimit        = 2048
	gopByteLimit          = 16 * 1024 * 1024
	bootstrapQueuePackets = 1024
	viewerQueueByteLimit  = 8 * 1024 * 1024
	liveQueuePackets      = 128
)

type viewer struct {
	queue      chan []byte
	done       chan struct{}
	once       sync.Once
	microphone bool
	bootstrap  atomic.Bool
	queueBytes atomic.Int64
}

func (v *viewer) close() { v.once.Do(func() { close(v.done) }) }

func (v *viewer) consumed(p []byte) {
	v.queueBytes.Add(-int64(len(p)))
	if len(v.queue) <= liveQueuePackets {
		v.bootstrap.Store(false)
	}
}

type source struct {
	connection         sourceConnection
	rtpHealth          sourceRTPHealth
	cfg                sourceConfig
	server             *gortsplib.Server
	ctx                context.Context
	cancel             context.CancelFunc
	stopped            chan struct{}
	mu                 sync.RWMutex
	client             *gortsplib.Client
	stream             *gortsplib.ServerStream
	viewers            map[*viewer]bool
	mic                *viewer
	back               *description.Media
	backFormat         *format.G711
	micSeq             uint16
	micTS              uint32
	started            time.Time
	video              map[string]any
	audio              map[string]any
	gop                [][]byte
	gopBytes           int
	gopEvictions       uint64
	gopResetReason     string
	videoMedia         *description.Media
	videoSSRC          atomic.Uint32
	haveVideoSSRC      atomic.Bool
	rtcpSender         uint32
	keyframeAt         time.Time
	keyframeRequests   uint64
	keyframeErrors     uint64
	keyframeObservedMS int64
	keyframeNotify     chan struct{}
	connected          atomic.Int32
	attempts           atomic.Uint64
	packets            atomic.Uint64
	bytes              atomic.Uint64
	drops              atomic.Uint64
	micPackets         atomic.Uint64
	relayFailures      atomic.Uint64
	relayOverflows     atomic.Uint64
	relayLastLog       atomic.Int64
	relayLastRole      atomic.Value
}

func newSource(c sourceConfig, server *gortsplib.Server) *source {
	ctx, cancel := context.WithCancel(context.Background())
	return &source{cfg: c, server: server, ctx: ctx, cancel: cancel, stopped: make(chan struct{}), viewers: map[*viewer]bool{}, started: time.Now(), rtcpSender: rand.Uint32() | 1, keyframeNotify: make(chan struct{})}
}

// Request an I-frame over the existing transport, without changing the encoder.
// Unsupported cameras keep working from the cached GOP. Coalesce tile joins.
func (s *source) requestKeyframe() bool {
	s.mu.Lock()
	client, media, ssrc := s.client, s.videoMedia, s.videoSSRC.Load()
	if client == nil || media == nil || !s.haveVideoSSRC.Load() || !s.keyframeAt.IsZero() && time.Since(s.keyframeAt) < 3*time.Second {
		s.mu.Unlock()
		return false
	}
	s.keyframeAt = time.Now()
	s.keyframeRequests++
	s.keyframeObservedMS = 0
	s.mu.Unlock()
	err := client.WritePacketRTCP(media, &rtcp.PictureLossIndication{SenderSSRC: s.rtcpSender, MediaSSRC: ssrc})
	if err != nil {
		s.mu.Lock()
		s.keyframeErrors++
		s.mu.Unlock()
	}
	return err == nil
}
func (s *source) stop() { s.cancel(); <-s.stopped }
func (s *source) status() map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	lastRole, _ := s.relayLastRole.Load().(string)
	span := int64(0)
	if len(s.gop) > 1 {
		span = (int64(binary.BigEndian.Uint64(s.gop[len(s.gop)-1][2:10])) - int64(binary.BigEndian.Uint64(s.gop[0][2:10]))) / 1000
	}
	return map[string]any{"connection": s.connection.snapshot(), "rtp": s.rtpHealth.snapshot(time.Now()), "keyframe_requests": s.keyframeRequests, "keyframe_write_errors": s.keyframeErrors, "keyframe_observed_after_request_ms": s.keyframeObservedMS, "keep_alive": true, "gop_cache_bytes": s.gopBytes, "gop_cache_packets": len(s.gop), "gop_cache_limit_bytes": gopByteLimit, "gop_cache_limit_packets": gopPacketLimit, "gop_cache_span_ms": span, "bootstrap_ready": len(s.gop) > 0, "gop_cache_evictions": s.gopEvictions, "gop_cache_reset_reason": s.gopResetReason, "rtsp_reader_failures": s.relayFailures.Load(), "rtsp_queue_overflows": s.relayOverflows.Load(), "last_failed_reader": lastRole, "rtsp_write_queue_packets": relayWriteQueueSize, "live_queue_packets": liveQueuePackets, "camera_id": s.cfg.ID, "paths": s.cfg.Paths, "upstream_connections": s.connected.Load(), "connection_attempts": s.attempts.Load(), "packets": s.packets.Load(), "bytes": s.bytes.Load(), "slow_viewers_closed": s.drops.Load(), "microphone_packets": s.micPackets.Load(), "microphone_supported": s.backFormat != nil, "microphone_active": s.mic != nil, "viewers": len(s.viewers), "ready": s.stream != nil, "video": s.video, "audio": s.audio}
}
func (s *source) run() {
	defer close(s.stopped)
	delay := time.Second
	for {
		if s.ctx.Err() != nil {
			return
		}
		start := time.Now()
		s.readOnce()
		if s.ctx.Err() != nil {
			return
		}
		if time.Since(start) > 30*time.Second {
			delay = time.Second
		}
		select {
		case <-s.ctx.Done():
			return
		case <-time.After(delay):
		}
		if delay < 16*time.Second {
			delay *= 2
		}
	}
}
func (s *source) readOnce() (result error) {
	s.connection.begin(s.attempts.Add(1), s.cfg.Microphone)
	defer func() { s.reportConnectionError(result) }()
	u, e := base.ParseURL(s.cfg.URL)
	if e != nil {
		return e
	}
	protocol := gortsplib.ProtocolTCP
	c := &gortsplib.Client{Scheme: u.Scheme, Host: u.Host, Protocol: &protocol, ReadTimeout: 8 * time.Second, WriteTimeout: 5 * time.Second, RequestBackChannels: s.cfg.Microphone}
	s.connection.stage("client_start")
	if e = c.Start(); e != nil {
		return e
	}
	var once sync.Once
	closeClient := func() { once.Do(func() { c.Close() }) }
	c.OnDecodeError = func(err error) { s.handleSourceDecodeError(err, time.Now(), closeClient) }
	c.OnRequest = s.connection.request
	c.OnResponse = s.handleRTSPResponse
	done := make(chan struct{})
	go func() {
		select {
		case <-s.ctx.Done():
			closeClient()
		case <-done:
		}
	}()
	defer close(done)
	defer closeClient()
	s.connection.stage("describe")
	desc, response, e := c.Describe(u)
	if e != nil && response != nil && (response.StatusCode == 400 || response.StatusCode == 501 || response.StatusCode == 551) && c.RequestBackChannels {
		c.RequestBackChannels = false
		s.connection.withoutBackchannel()
		log.Printf("rtsp_optional_backchannel_rejected camera_id=%s status=%d; retrying DESCRIBE on the same connection", s.cfg.ID, response.StatusCode)
		desc, _, e = c.Describe(u)
	}
	if e != nil {
		return e
	}
	forward := &description.Session{}
	var back *description.Media
	var videoMedia *description.Media
	var backFormat *format.G711
	handlers := map[format.Format]func(*rtp.Packet){}
	for _, media := range desc.Medias {
		if media.IsBackChannel {
			if c.RequestBackChannels {
				for _, f := range media.Formats {
					if g, ok := f.(*format.G711); ok && g.ClockRate() == 8000 && g.ChannelCount == 1 {
						back = media
						backFormat = g
						break
					}
				}
			}
			continue
		}
		s.connection.stage("setup_" + string(media.Type))
		if _, e = c.Setup(desc.BaseURL, media, 0, 0); e != nil {
			return e
		}
		forward.Medias = append(forward.Medias, media)
		for _, f := range media.Formats {
			handlers[f] = s.makeHandler(c, media, f)
			if media.Type == description.MediaTypeVideo && handlers[f] != nil {
				videoMedia = media
			}
		}
	}
	if len(forward.Medias) == 0 {
		return errors.New("no media")
	}
	if back != nil {
		s.connection.stage("setup_backchannel")
		if _, e = c.Setup(desc.BaseURL, back, 0, 0); e != nil {
			back = nil
			backFormat = nil
		}
	}
	stream := &gortsplib.ServerStream{Server: s.server, Desc: forward}
	s.connection.stage("relay_initialize")
	if e = stream.Initialize(); e != nil {
		return e
	}
	s.mu.Lock()
	s.stream = stream
	s.client = c
	s.videoMedia = videoMedia
	s.videoSSRC.Store(0)
	s.haveVideoSSRC.Store(false)
	s.keyframeAt = time.Time{}
	s.keyframeObservedMS = 0
	s.back = back
	s.backFormat = backFormat
	s.connected.Store(1)
	s.mu.Unlock()
	defer func() {
		closeClient()
		s.mu.Lock()
		s.stream = nil
		s.client = nil
		s.videoMedia = nil
		s.videoSSRC.Store(0)
		s.haveVideoSSRC.Store(false)
		s.back = nil
		s.backFormat = nil
		s.connected.Store(0)
		views := s.viewers
		s.viewers = map[*viewer]bool{}
		s.mic = nil
		s.gop = nil
		s.gopBytes = 0
		s.video = nil
		s.audio = nil
		s.mu.Unlock()
		for v := range views {
			v.close()
		}
		stream.Close()
	}()
	c.OnPacketRTPAny(func(media *description.Media, f format.Format, p *rtp.Packet) {
		if media.IsBackChannel {
			return
		}
		s.packets.Add(1)
		s.bytes.Add(uint64(p.MarshalSize()))
		if media == videoMedia {
			s.rtpHealth.videoPacket(time.Now())
			s.videoSSRC.Store(p.SSRC)
			first := !s.haveVideoSSRC.Swap(true)
			if first {
				s.mu.RLock()
				missing := s.video == nil
				s.mu.RUnlock()
				if missing {
					go s.requestKeyframe()
				}
			}
		}
		stream.WritePacketRTP(media, p)
		if handler := handlers[f]; handler != nil {
			handler(p)
		}
	})
	s.rtpHealth.begin(forward, time.Now())
	s.connection.stage("play")
	if _, e = c.Play(nil); e != nil {
		return e
	}
	s.connection.ready()
	return c.Wait()
}
func frame(kind byte, key bool, stamp int64, payload []byte) []byte {
	out := make([]byte, 10+len(payload))
	out[0] = kind
	if key {
		out[1] = 1
	}
	binary.BigEndian.PutUint64(out[2:10], uint64(stamp))
	copy(out[10:], payload)
	return out
}
func (s *source) setVideo(v map[string]any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.video != nil && s.video["codec"] != v["codec"] {
		s.gop = nil
		s.gopBytes = 0
		for viewer := range s.viewers {
			viewer.close()
		}
		s.viewers = map[*viewer]bool{}
		s.mic = nil
	}
	s.video = v
}
func (s *source) setAudio(v map[string]any) { s.mu.Lock(); s.audio = v; s.mu.Unlock() }
func h264VideoMetadata(codec string, sps []byte) map[string]any {
	video := map[string]any{"codec": codec, "codec_name": "h264", "format": "annexb", "parameters_ready": true}
	var parsed codech264.SPS
	if parsed.Unmarshal(sps) == nil {
		video["width"] = parsed.Width()
		video["height"] = parsed.Height()
		if fps := parsed.FPS(); fps > 0 && fps <= 240 {
			video["fps"] = fps
		}
	}
	return video
}
func (s *source) makeHandler(c *gortsplib.Client, media *description.Media, f format.Format) func(*rtp.Packet) {
	pts := func(p *rtp.Packet) int64 {
		v, ok := c.PacketPTS(media, p)
		if !ok {
			return 0
		}
		rate := int64(f.ClockRate())
		return (v/rate)*1000000 + (v%rate)*1000000/rate
	}
	switch f := f.(type) {
	case *format.H265:
		return s.makeH265Handler(c, media, f)
	case *format.H264:
		decoder, e := f.CreateDecoder()
		if e != nil {
			return nil
		}
		sps, pps := f.SafeParams()
		codec := ""
		if len(sps) >= 4 && len(pps) > 0 {
			codec = "avc1." + hex.EncodeToString(sps[1:4])
			s.setVideo(h264VideoMetadata(codec, sps))
		}
		return func(p *rtp.Packet) {
			// Initialize timestamp tracking on the first fragment, not only the last.
			stamp := pts(p)
			aus, e := decoder.Decode(p)
			if e != nil {
				return
			}
			key := false
			for _, n := range aus {
				if len(n) > 0 && n[0]&31 == 5 {
					key = true
				}
				if len(n) > 0 && n[0]&31 == 7 {
					sps = append([]byte{}, n...)
				}
				if len(n) > 0 && n[0]&31 == 8 {
					pps = append([]byte{}, n...)
				}
			}
			for _, n := range aus {
				if len(n) > 0 && (n[0]&31 == 7 || n[0]&31 == 8) && len(sps) > 0 && len(pps) > 0 {
					f.SafeSetParams(sps, pps)
					break
				}
			}
			if len(sps) >= 4 && len(pps) > 0 {
				current := "avc1." + hex.EncodeToString(sps[1:4])
				if current != codec {
					codec = current
					s.setVideo(h264VideoMetadata(codec, sps))
				}
			}
			payload := []byte{}
			if key {
				for _, n := range [][]byte{sps, pps} {
					if len(n) > 0 {
						payload = append(payload, 0, 0, 0, 1)
						payload = append(payload, n...)
					}
				}
			}
			for _, n := range aus {
				payload = append(payload, 0, 0, 0, 1)
				payload = append(payload, n...)
			}
			s.broadcast(frame(1, key, stamp, payload), key)
		}
	case *format.G711:
		if f.ClockRate() != 8000 || f.ChannelCount != 1 {
			return nil
		}
		codec := "pcma"
		if f.MULaw {
			codec = "pcmu"
		}
		s.setAudio(map[string]any{"codec": codec, "sampleRate": 8000, "channels": 1})
		return func(p *rtp.Packet) { s.broadcast(frame(2, false, pts(p), p.Payload), false) }
	case *format.Opus:
		decoder, e := f.CreateDecoder()
		if e != nil {
			return nil
		}
		s.setAudio(map[string]any{"codec": "opus", "sampleRate": 48000, "channels": 2})
		return func(p *rtp.Packet) {
			au, e := decoder.Decode(p)
			if e == nil {
				s.broadcast(frame(2, false, pts(p), au), false)
			}
		}
	case *format.MPEG4Audio:
		decoder, e := f.CreateDecoder()
		if e != nil {
			return nil
		}
		asc, e := f.Config.Marshal()
		if e != nil {
			return nil
		}
		s.setAudio(map[string]any{"codec": fmt.Sprintf("mp4a.40.%d", f.Config.Type), "sampleRate": f.ClockRate(), "channels": f.Config.ChannelCount, "config": base64.StdEncoding.EncodeToString(asc)})
		return func(p *rtp.Packet) {
			aus, e := decoder.Decode(p)
			if e != nil {
				return
			}
			for i, au := range aus {
				s.broadcast(frame(2, false, pts(p)+int64(i)*1024*1000000/int64(f.ClockRate()), au), false)
			}
		}
	}
	return nil
}
func (s *source) broadcast(p []byte, key bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if p[0] == 1 {
		if key {
			if s.keyframeNotify != nil {
				close(s.keyframeNotify)
			}
			s.keyframeNotify = make(chan struct{})
			if !s.keyframeAt.IsZero() && s.keyframeObservedMS == 0 {
				s.keyframeObservedMS = max(1, time.Since(s.keyframeAt).Milliseconds())
			}
			s.gop = nil
			s.gopBytes = 0
			s.gopResetReason = ""
		}
		if key || len(s.gop) > 0 {
			s.gop = append(s.gop, p)
			s.gopBytes += len(p)
			if s.gopBytes > gopByteLimit || len(s.gop) > gopPacketLimit {
				s.gopEvictions++
				s.gopResetReason = "packet_limit"
				if s.gopBytes > gopByteLimit {
					s.gopResetReason = "byte_limit"
				}
				s.gop = nil
				s.gopBytes = 0
			}
		}
	}
	for v := range s.viewers {
		if (!v.bootstrap.Load() && len(v.queue) >= liveQueuePackets) || v.queueBytes.Load()+int64(len(p)) > viewerQueueByteLimit {
			s.drops.Add(1)
			v.close()
			delete(s.viewers, v)
			continue
		}
		v.queueBytes.Add(int64(len(p)))
		select {
		case <-v.done:
			v.queueBytes.Add(-int64(len(p)))
			delete(s.viewers, v)
		case v.queue <- p:
		default:
			v.queueBytes.Add(-int64(len(p)))
			s.drops.Add(1)
			v.close()
			delete(s.viewers, v)
		}
	}
}
func (s *source) attach(microphone bool) (*viewer, map[string]any, [][]byte, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.stream == nil {
		return nil, nil, nil, errors.New("camera_connecting")
	}
	if s.video == nil {
		return nil, nil, nil, errors.New("h264_or_h265_video_metadata_required")
	}
	if len(s.viewers) >= 32 {
		return nil, nil, nil, errors.New("viewer_limit")
	}
	if microphone && (s.backFormat == nil || s.mic != nil) {
		return nil, nil, nil, errors.New("microphone_unavailable_or_busy")
	}
	v := &viewer{queue: make(chan []byte, bootstrapQueuePackets), done: make(chan struct{}), microphone: microphone}
	v.bootstrap.Store(len(s.gop) > 0)
	s.viewers[v] = true
	if microphone {
		s.mic = v
		s.micTS = uint32(time.Since(s.started) / (time.Second / 8000))
	}
	hello := map[string]any{"type": "hello", "protocol": "edgertc/1", "camera_id": s.cfg.ID, "video": s.video, "audio": s.audio, "microphone": map[string]any{"active": microphone, "supported": s.backFormat != nil, "sampleRate": 8000, "format": "pcm_s16le"}}
	hello["bootstrap_packets"] = len(s.gop)
	hello["bootstrap_bytes"] = s.gopBytes
	// Decode the cached GOP for references, but start display at its newest frame.
	if len(s.gop) > 0 {
		last := s.gop[len(s.gop)-1]
		if len(last) >= 10 {
			hello["bootstrap_timestamp_us"] = int64(binary.BigEndian.Uint64(last[2:10]))
		}
	}
	return v, hello, append([][]byte{}, s.gop...), nil
}

// Give camera feedback a short opportunity to replace a large old GOP before
// writing it into the Cloud/TCP buffers, where it cannot be recalled later.
func (s *source) refreshBootstrap(v *viewer, hello map[string]any, cache [][]byte, timeout time.Duration) [][]byte {
	stamp := func(packets [][]byte) int64 {
		if len(packets) == 0 {
			return -1
		}
		return int64(binary.BigEndian.Uint64(packets[0][2:10]))
	}
	previous := stamp(cache)
	refresh := func() ([][]byte, <-chan struct{}) {
		s.mu.Lock()
		defer s.mu.Unlock()
		if len(s.gop) == 0 || stamp(s.gop) == previous {
			return nil, s.keyframeNotify
		}
		for {
			select {
			case p := <-v.queue:
				v.consumed(p)
			default:
				goto drained
			}
		}
	drained:
		v.bootstrap.Store(true)
		fresh := append([][]byte{}, s.gop...)
		hello["bootstrap_packets"] = len(fresh)
		hello["bootstrap_bytes"] = s.gopBytes
		hello["bootstrap_timestamp_us"] = int64(binary.BigEndian.Uint64(fresh[len(fresh)-1][2:10]))
		hello["bootstrap_refreshed"] = true
		return fresh, nil
	}
	fresh, notify := refresh()
	if fresh != nil {
		return fresh
	}
	var done <-chan struct{}
	if s.ctx != nil {
		done = s.ctx.Done()
	}
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case <-notify:
		if fresh, _ = refresh(); fresh != nil {
			return fresh
		}
	case <-timer.C:
	case <-done:
	case <-v.done:
	}
	return cache
}

func (s *source) detach(v *viewer) {
	s.mu.Lock()
	delete(s.viewers, v)
	if s.mic == v {
		s.mic = nil
	}
	s.mu.Unlock()
	v.close()
}
func (s *source) writePCM(v *viewer, pcm []byte) error {
	s.mu.Lock()
	if s.mic != v || s.client == nil || s.backFormat == nil {
		s.mu.Unlock()
		return errors.New("microphone_inactive")
	}
	be := make([]byte, len(pcm))
	for i := 0; i < len(pcm); i += 2 {
		be[i] = pcm[i+1]
		be[i+1] = pcm[i]
	}
	var encoded []byte
	var e error
	if s.backFormat.MULaw {
		encoded, e = g711.Mulaw(be).Marshal()
	} else {
		encoded, e = g711.Alaw(be).Marshal()
	}
	if e != nil {
		s.mu.Unlock()
		return e
	}
	s.micSeq++
	elapsed := uint32(time.Since(s.started) / (time.Second / 8000))
	if int32(elapsed-s.micTS) > 1600 {
		s.micTS = elapsed
	} else {
		s.micTS += uint32(len(pcm) / 2)
	}
	p := &rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: s.backFormat.PayloadType(), SequenceNumber: s.micSeq, Timestamp: s.micTS, SSRC: 0x45444745}, Payload: encoded}
	client, back := s.client, s.back
	s.mu.Unlock()
	if e = client.WritePacketRTP(back, p); e != nil {
		return e
	}
	s.micPackets.Add(1)
	return nil
}
