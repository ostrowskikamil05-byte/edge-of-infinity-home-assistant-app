package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"strconv"
	"sync"
	"time"

	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/liberrors"
)

// Dynamic RTP numbers are meaningful only within the camera's SDP.
// Retain evidence; never guess a codec from a numeric payload type.
type sourceRTPHealth struct {
	mu                                            sync.Mutex
	started, lastVideo, lastRefresh, lastOtherLog time.Time
	expected                                      []map[string]any
	unknown                                       [128]uint64
	lastLog                                       [128]time.Time
	decodeErrors, connectionUnknown, refreshes    uint64
	videoExpected                                 bool
}

func (h *sourceRTPHealth) begin(desc *description.Session, now time.Time) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.started, h.lastVideo = now, time.Time{}
	h.connectionUnknown, h.videoExpected = 0, false
	h.expected = nil
	for _, media := range desc.Medias {
		if media.IsBackChannel {
			continue
		}
		for _, f := range media.Formats {
			if len(h.expected) < 32 {
				h.expected = append(h.expected, map[string]any{"media": string(media.Type), "codec": f.Codec(), "payload_type": f.PayloadType(), "clock_rate": f.ClockRate()})
			}
			if media.Type == description.MediaTypeVideo && (f.Codec() == "H264" || f.Codec() == "H265") {
				h.videoExpected = true
			}
		}
	}
}

func (h *sourceRTPHealth) videoPacket(now time.Time) { h.mu.Lock(); h.lastVideo = now; h.mu.Unlock() }

func (h *sourceRTPHealth) decodeError(err error, now time.Time) (map[string]any, bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.decodeErrors++
	var unknown liberrors.ErrClientRTPPacketUnknownPayloadType
	if !errors.As(err, &unknown) || unknown.PayloadType > 127 {
		if !h.lastOtherLog.IsZero() && now.Sub(h.lastOtherLog) < 30*time.Second {
			return nil, false
		}
		h.lastOtherLog = now
		return map[string]any{"event": "rtp_source_decode_warning", "code": fmt.Sprintf("%T", err), "decode_errors": h.decodeErrors}, false
	}
	pt := unknown.PayloadType
	h.unknown[pt]++
	h.connectionUnknown++
	lastVideo := h.lastVideo
	if lastVideo.IsZero() {
		lastVideo = h.started
	}
	refresh := h.videoExpected && !lastVideo.IsZero() && now.Sub(lastVideo) >= 5*time.Second && h.connectionUnknown >= 20 && (h.lastRefresh.IsZero() || now.Sub(h.lastRefresh) >= 60*time.Second)
	if refresh {
		h.lastRefresh = now
		h.refreshes++
	}
	if !refresh && !h.lastLog[pt].IsZero() && now.Sub(h.lastLog[pt]) < 30*time.Second {
		return nil, false
	}
	h.lastLog[pt] = now
	return map[string]any{"event": "rtp_unknown_payload", "code": "payload_not_declared_in_media_sdp", "payload_type": pt, "count": h.unknown[pt],
		"declared_formats": h.expected, "video_packet_age_ms": max(0, now.Sub(lastVideo).Milliseconds()), "description_refresh": refresh, "codec_remapped": false}, refresh
}

func (h *sourceRTPHealth) snapshot(now time.Time) map[string]any {
	h.mu.Lock()
	defer h.mu.Unlock()
	unknown := map[string]uint64{}
	for pt, count := range h.unknown {
		if count > 0 {
			unknown[strconv.Itoa(pt)] = count
		}
	}
	age := int64(-1)
	if !h.lastVideo.IsZero() {
		age = max(0, now.Sub(h.lastVideo).Milliseconds())
	}
	return map[string]any{"decode_errors": h.decodeErrors, "unknown_payload_types": unknown, "declared_formats": h.expected,
		"description_refreshes": h.refreshes, "last_video_packet_age_ms": age}
}

func (s *source) handleSourceDecodeError(err error, now time.Time, closeCurrent func()) {
	data, refresh := s.rtpHealth.decodeError(err, now)
	if data != nil {
		data["camera_id"] = s.cfg.ID
		data["severity"] = "warning"
		if encoded, e := json.Marshal(data); e == nil {
			log.Printf("rtp_source %s", encoded)
		}
	}
	// The callback runs on the RTSP reader goroutine. Close asynchronously so
	// Close can wait for it. Existing cleanup finishes before run creates the
	// next camera connection; healthy video does not reconnect.
	if refresh {
		go closeCurrent()
	}
}
