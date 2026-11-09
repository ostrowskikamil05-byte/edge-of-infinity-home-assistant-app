package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log"
	"net"
	"net/url"
	"regexp"
	"strconv"
	"strings"
	"sync"
	"syscall"
	"time"

	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/liberrors"
)

type sourceConnection struct {
	mu                        sync.Mutex
	phase, method, state      string
	status                    int
	attempt, failures         uint64
	started                   time.Time
	backchannel               bool
	formats                   []map[string]any
	optionalAudioFiltered     []map[string]any
	lastError                 map[string]any
	lastLog                   time.Time
	lastLogCode, lastLogPhase string
}

func (h *sourceConnection) begin(attempt uint64, microphone bool) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.phase, h.method, h.state, h.status = "parse_url", "", "connecting", 0
	h.attempt, h.started, h.backchannel = attempt, time.Now(), microphone
	h.formats = nil
	h.optionalAudioFiltered = nil
}
func (h *sourceConnection) stage(phase string) {
	h.mu.Lock()
	h.phase, h.method, h.status = phase, "", 0
	h.mu.Unlock()
}
func (h *sourceConnection) request(r *base.Request) {
	h.mu.Lock()
	h.method, h.status = string(r.Method), 0
	h.mu.Unlock()
}
func (h *sourceConnection) response(r *base.Response) {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.status = int(r.StatusCode)
	if h.method == "DESCRIBE" && r.StatusCode == base.StatusOK {
		h.formats = sdpFormatOverview(r.Body)
	}
}
func (h *sourceConnection) isSDPResponse(r *base.Response) bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.method == "DESCRIBE" && r.StatusCode == base.StatusOK && len(r.Header["Content-Type"]) == 1 &&
		strings.TrimSpace(strings.SplitN(r.Header["Content-Type"][0], ";", 2)[0]) == "application/sdp"
}
func (h *sourceConnection) filteredOptionalAudio(changes []map[string]any) {
	h.mu.Lock()
	h.optionalAudioFiltered = changes
	h.mu.Unlock()
}
func (h *sourceConnection) withoutBackchannel() { h.mu.Lock(); h.backchannel = false; h.mu.Unlock() }
func (h *sourceConnection) ready() {
	h.mu.Lock()
	h.phase, h.state = "stream", "connected"
	h.lastError = nil
	h.mu.Unlock()
}

var errorURL = regexp.MustCompile(`(?i)(?:rtsps?|https?)://[^\s<>"']+`)
var audioConfigHex = regexp.MustCompile(`(?i)^[0-9a-f]{1,96}$`)

// Inspect only bounded codec declarations, never SDP control URLs or auth headers.
// This remains available even if the library cannot parse an advertised codec.
func sdpFormatOverview(body []byte) []map[string]any {
	if len(body) > 65536 {
		body = body[:65536]
	}
	var formats []map[string]any
	configs := map[string]string{}
	media, index := "", 0
	directions := map[int]string{}
	for _, line := range strings.Split(string(body), "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "m=") {
			index++
			fields := strings.Fields(line[2:])
			media = "other"
			if len(fields) > 0 {
				switch fields[0] {
				case "video", "audio", "application", "text", "message":
					media = fields[0]
				}
			}
			continue
		}
		switch line {
		case "a=sendonly", "a=recvonly", "a=sendrecv", "a=inactive":
			directions[index] = strings.TrimPrefix(line, "a=")
		}
		if strings.HasPrefix(line, "a=rtpmap:") && len(formats) < 32 {
			fields := strings.Fields(strings.TrimPrefix(line, "a=rtpmap:"))
			if len(fields) != 2 || len(fields[1]) > 64 {
				continue
			}
			pt, err := strconv.Atoi(fields[0])
			if err != nil || pt < 0 || pt > 127 {
				continue
			}
			codec := strings.Split(fields[1], "/")
			if len(codec) < 2 || len(codec) > 3 || !regexpCodecName.MatchString(codec[0]) {
				continue
			}
			rate, err := strconv.Atoi(codec[1])
			if err != nil || rate < 1 || rate > 1000000 {
				continue
			}
			row := map[string]any{"media": media, "media_index": index, "payload_type": pt, "codec": codec[0], "clock_rate": rate}
			if len(codec) == 3 {
				if n, err := strconv.Atoi(codec[2]); err == nil && n > 0 && n < 64 {
					row["channels"] = n
				}
			}
			formats = append(formats, row)
		}
		if strings.HasPrefix(line, "a=fmtp:") {
			fields := strings.SplitN(strings.TrimPrefix(line, "a=fmtp:"), " ", 2)
			if len(fields) != 2 {
				continue
			}
			pt, err := strconv.Atoi(fields[0])
			if err != nil {
				continue
			}
			for _, parameter := range strings.Split(fields[1], ";") {
				pair := strings.SplitN(strings.TrimSpace(parameter), "=", 2)
				if len(pair) != 2 || !strings.EqualFold(pair[0], "config") || !audioConfigHex.MatchString(pair[1]) {
					continue
				}
				configs[strconv.Itoa(index)+":"+strconv.Itoa(pt)] = pair[1]
			}
		}
	}
	for _, row := range formats {
		if direction := directions[row["media_index"].(int)]; direction != "" {
			row["direction"] = direction
		}
		if config := configs[fmt.Sprint(row["media_index"])+":"+fmt.Sprint(row["payload_type"])]; config != "" {
			row["audio_specific_config_hex"] = config
		}
	}
	return formats
}

var regexpCodecName = regexp.MustCompile(`^[A-Za-z0-9_-]{1,40}$`)

func connectionErrorCode(err error, status int) string {
	var sdp liberrors.ErrClientSDPInvalid
	if errors.As(err, &sdp) {
		return "sdp_invalid"
	}
	var bad liberrors.ErrClientBadStatusCode
	if errors.As(err, &bad) {
		status = int(bad.Code)
	}
	switch status {
	case 401:
		return "rtsp_authentication_failed"
	case 403:
		return "rtsp_access_denied"
	case 404:
		return "rtsp_stream_not_found"
	case 453, 503:
		return "rtsp_source_busy"
	case 461:
		return "rtsp_transport_rejected"
	case 400, 405, 451, 454, 500, 501, 551:
		return "rtsp_request_rejected"
	}
	var network net.Error
	if errors.As(err, &network) && network.Timeout() {
		return "rtsp_network_timeout"
	}
	if errors.Is(err, syscall.ECONNREFUSED) {
		return "rtsp_connection_refused"
	}
	if errors.Is(err, syscall.EHOSTUNREACH) || errors.Is(err, syscall.ENETUNREACH) {
		return "rtsp_network_unreachable"
	}
	if errors.Is(err, io.EOF) {
		return "rtsp_connection_closed"
	}
	var missing liberrors.ErrClientContentTypeMissing
	var unsupported liberrors.ErrClientContentTypeUnsupported
	if errors.As(err, &missing) || errors.As(err, &unsupported) {
		return "rtsp_description_content_type"
	}
	return "rtsp_negotiation_failed"
}

func safeSDPError(err error, sourceURL string) string {
	var invalid liberrors.ErrClientSDPInvalid
	if !errors.As(err, &invalid) {
		return ""
	}
	text := errorURL.ReplaceAllString(invalid.Error(), "[URL redacted]")
	if u, e := url.Parse(sourceURL); e == nil && u.User != nil {
		if password, ok := u.User.Password(); ok && password != "" {
			for _, value := range []string{password, url.QueryEscape(password), url.PathEscape(password)} {
				text = strings.ReplaceAll(text, value, "[redacted]")
			}
		}
	}
	text = strings.Map(func(r rune) rune {
		if r < 32 || r == 127 {
			return ' '
		}
		return r
	}, text)
	if len(text) > 384 {
		text = text[:384]
	}
	return text
}

func (h *sourceConnection) failed(err error, sourceURL string) map[string]any {
	h.mu.Lock()
	defer h.mu.Unlock()
	h.failures++
	h.state = "retrying"
	code := connectionErrorCode(err, h.status)
	h.lastError = map[string]any{"code": code, "phase": h.phase, "rtsp_method": h.method, "rtsp_status": h.status,
		"error_type": fmt.Sprintf("%T", err), "attempt": h.attempt, "failed_at": time.Now().UTC().Format(time.RFC3339),
		"elapsed_ms": time.Since(h.started).Milliseconds(), "backchannel_requested": h.backchannel}
	if detail := safeSDPError(err, sourceURL); detail != "" {
		h.lastError["detail"] = detail
	}
	if h.lastLogCode == code && h.lastLogPhase == h.phase && time.Since(h.lastLog) < 30*time.Second {
		return nil
	}
	h.lastLog, h.lastLogCode, h.lastLogPhase = time.Now(), code, h.phase
	return map[string]any{"event": "rtsp_source_error", "severity": "warning", "failure_count": h.failures, "failure": h.lastError, "sdp_formats": h.formats}
}
func (h *sourceConnection) snapshot() map[string]any {
	h.mu.Lock()
	defer h.mu.Unlock()
	var lastError any
	if h.lastError != nil {
		lastError = h.lastError
	}
	return map[string]any{"state": h.state, "phase": h.phase, "rtsp_method": h.method, "rtsp_status": h.status,
		"attempt": h.attempt, "backchannel_requested": h.backchannel, "failure_count": h.failures, "last_error": lastError, "sdp_formats": h.formats,
		"optional_audio_filtered": h.optionalAudioFiltered}
}
func (s *source) reportConnectionError(err error) {
	var terminated liberrors.ErrClientTerminated
	if err == nil || s.ctx.Err() != nil || errors.As(err, &terminated) {
		return
	}
	if data := s.connection.failed(err, s.cfg.URL); data != nil {
		data["camera_id"] = s.cfg.ID
		if encoded, e := json.Marshal(data); e == nil {
			log.Printf("rtsp_source %s", encoded)
		}
	}
}
