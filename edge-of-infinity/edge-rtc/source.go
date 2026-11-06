package main

import (
	"context"
	"encoding/base64"
	"encoding/binary"
	"encoding/hex"
	"errors"
	"sync"
	"sync/atomic"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/g711"
	"github.com/pion/rtp"
)

type viewer struct {
	queue      chan []byte
	done       chan struct{}
	once       sync.Once
	microphone bool
}

func (v *viewer) close() { v.once.Do(func() { close(v.done) }) }

type source struct {
	cfg        sourceConfig
	server     *gortsplib.Server
	ctx        context.Context
	cancel     context.CancelFunc
	stopped    chan struct{}
	mu         sync.RWMutex
	client     *gortsplib.Client
	stream     *gortsplib.ServerStream
	viewers    map[*viewer]bool
	mic        *viewer
	back       *description.Media
	backFormat *format.G711
	micSeq     uint16
	micTS      uint32
	started    time.Time
	video      map[string]any
	audio      map[string]any
	gop        [][]byte
	gopBytes   int
	connected  atomic.Int32
	attempts   atomic.Uint64
	packets    atomic.Uint64
	bytes      atomic.Uint64
	drops      atomic.Uint64
	micPackets atomic.Uint64
}

func newSource(c sourceConfig, server *gortsplib.Server) *source {
	ctx, cancel := context.WithCancel(context.Background())
	return &source{cfg: c, server: server, ctx: ctx, cancel: cancel, stopped: make(chan struct{}), viewers: map[*viewer]bool{}, started: time.Now()}
}
func (s *source) stop() { s.cancel(); <-s.stopped }
func (s *source) status() map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return map[string]any{"camera_id": s.cfg.ID, "upstream_connections": s.connected.Load(), "connection_attempts": s.attempts.Load(), "packets": s.packets.Load(), "bytes": s.bytes.Load(), "slow_viewers_closed": s.drops.Load(), "microphone_packets": s.micPackets.Load(), "microphone_supported": s.backFormat != nil, "microphone_active": s.mic != nil, "viewers": len(s.viewers), "ready": s.stream != nil, "video": s.video, "audio": s.audio}
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
func (s *source) readOnce() error {
	u, e := base.ParseURL(s.cfg.URL)
	if e != nil {
		return e
	}
	protocol := gortsplib.ProtocolTCP
	c := &gortsplib.Client{Scheme: u.Scheme, Host: u.Host, Protocol: &protocol, ReadTimeout: 8 * time.Second, WriteTimeout: 5 * time.Second, RequestBackChannels: s.cfg.Microphone}
	s.attempts.Add(1)
	if e = c.Start(); e != nil {
		return e
	}
	var once sync.Once
	closeClient := func() { once.Do(func() { c.Close() }) }
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
	desc, response, e := c.Describe(u)
	if e != nil && response != nil && response.StatusCode == 551 && c.RequestBackChannels {
		c.RequestBackChannels = false
		desc, _, e = c.Describe(u)
	}
	if e != nil {
		return e
	}
	forward := &description.Session{}
	var back *description.Media
	var backFormat *format.G711
	handlers := map[format.Format]func(*rtp.Packet){}
	for _, media := range desc.Medias {
		if media.IsBackChannel {
			if s.cfg.Microphone {
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
		if _, e = c.Setup(desc.BaseURL, media, 0, 0); e != nil {
			return e
		}
		forward.Medias = append(forward.Medias, media)
		for _, f := range media.Formats {
			handlers[f] = s.makeHandler(c, media, f)
		}
	}
	if len(forward.Medias) == 0 {
		return errors.New("no media")
	}
	if back != nil {
		if _, e = c.Setup(desc.BaseURL, back, 0, 0); e != nil {
			back = nil
			backFormat = nil
		}
	}
	stream := &gortsplib.ServerStream{Server: s.server, Desc: forward}
	if e = stream.Initialize(); e != nil {
		return e
	}
	s.mu.Lock()
	s.stream = stream
	s.client = c
	s.back = back
	s.backFormat = backFormat
	s.connected.Store(1)
	s.mu.Unlock()
	defer func() {
		closeClient()
		s.mu.Lock()
		s.stream = nil
		s.client = nil
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
		stream.WritePacketRTP(media, p)
		if handler := handlers[f]; handler != nil {
			handler(p)
		}
	})
	if _, e = c.Play(nil); e != nil {
		return e
	}
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
		codec := "avc1.42e01f"
		if len(sps) >= 4 {
			codec = "avc1." + hex.EncodeToString(sps[1:4])
		}
		s.setVideo(map[string]any{"codec": codec, "codec_name": "h264", "format": "annexb"})
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
		s.setAudio(map[string]any{"codec": "mp4a.40.2", "sampleRate": f.ClockRate(), "channels": f.Config.ChannelCount, "config": base64.StdEncoding.EncodeToString(asc)})
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
			s.gop = nil
			s.gopBytes = 0
		}
		if key || len(s.gop) > 0 {
			s.gop = append(s.gop, p)
			s.gopBytes += len(p)
			if s.gopBytes > 16*1024*1024 || len(s.gop) > 180 {
				s.gop = nil
				s.gopBytes = 0
			}
		}
	}
	for v := range s.viewers {
		select {
		case <-v.done:
			delete(s.viewers, v)
		case v.queue <- p:
		default:
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
	v := &viewer{queue: make(chan []byte, 16), done: make(chan struct{}), microphone: microphone}
	s.viewers[v] = true
	if microphone {
		s.mic = v
		s.micTS = uint32(time.Since(s.started) / (time.Second / 8000))
	}
	hello := map[string]any{"type": "hello", "protocol": "edgertc/1", "camera_id": s.cfg.ID, "video": s.video, "audio": s.audio, "microphone": map[string]any{"active": microphone, "supported": s.backFormat != nil, "sampleRate": 8000, "format": "pcm_s16le"}}
	return v, hello, append([][]byte{}, s.gop...), nil
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
