package main

import (
	"encoding/json"
	"errors"
	"strings"
	"syscall"
	"testing"

	"github.com/bluenviron/gortsplib/v5/pkg/liberrors"
)

func TestConnectionFailureCodesAndSafeSDPDetail(t *testing.T) {
	cases := []struct {
		err    error
		status int
		want   string
	}{
		{liberrors.ErrClientBadStatusCode{Code: 401}, 0, "rtsp_authentication_failed"},
		{liberrors.ErrClientBadStatusCode{Code: 404}, 0, "rtsp_stream_not_found"},
		{liberrors.ErrClientBadStatusCode{Code: 503}, 0, "rtsp_source_busy"},
		{syscall.ECONNREFUSED, 0, "rtsp_connection_refused"},
		{errors.New("auth negotiation"), 401, "rtsp_authentication_failed"},
	}
	for _, c := range cases {
		if got := connectionErrorCode(c.err, c.status); got != c.want {
			t.Fatalf("code %q != %q", got, c.want)
		}
	}
	var health sourceConnection
	health.begin(7, true)
	health.stage("describe")
	err := liberrors.ErrClientSDPInvalid{Err: errors.New("unsupported audio config at rtsp://admin:camera%21secret@192.0.2.10/secret?token=hidden and camera!secret")}
	entry := health.failed(err, "rtsp://admin:camera%21secret@192.0.2.10/media/video1")
	encoded, _ := json.Marshal(entry)
	text := string(encoded)
	if !strings.Contains(text, "sdp_invalid") || strings.Contains(text, "camera!secret") || strings.Contains(text, "camera%21secret") || strings.Contains(text, "192.0.2.10") {
		t.Fatalf("unsafe or missing diagnostic: %s", text)
	}
}

func TestSDPOverviewIncludesAudioWithoutControlURLs(t *testing.T) {
	body := []byte("v=0\r\nm=video 0 RTP/AVP 104\r\na=rtpmap:104 H265/90000\r\na=control:rtsp://user:secret@192.0.2.1/video\r\nm=audio 0 RTP/AVP 108\r\na=fmtp:108 config=1408;mode=AAC-hbr\r\na=rtpmap:108 MPEG4-GENERIC/16000/1\r\n")
	rows := sdpFormatOverview(body)
	if len(rows) != 2 || rows[0]["codec"] != "H265" || rows[1]["audio_specific_config_hex"] != "1408" {
		t.Fatal(rows)
	}
	encoded, _ := json.Marshal(rows)
	if strings.Contains(string(encoded), "secret") || strings.Contains(string(encoded), "192.0.2.1") {
		t.Fatal("SDP control URI leaked")
	}
}

func TestRTSPRejectIsReportedBeforeAnyVideoAndWithoutURL(t *testing.T) {
	camera := newFake(t)
	camera.describeStatus.Store(404)
	cfg := config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("e", 40), Sources: []sourceConfig{{ID: "unv", URL: "rtsp://" + camera.server.RTSPAddress + "/camera", Paths: []string{"unv_main"}}}}
	a, err := newApp(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer a.close()
	s := a.lookup("unv_main")
	eventually(t, func() bool {
		failure, ok := s.connection.snapshot()["last_error"].(map[string]any)
		return ok && failure["code"] == "rtsp_stream_not_found"
	})
	state := s.connection.snapshot()
	failure := state["last_error"].(map[string]any)
	if failure["phase"] != "describe" || failure["rtsp_method"] != "DESCRIBE" || failure["rtsp_status"] != 404 || s.packets.Load() != 0 || s.connected.Load() != 0 {
		t.Fatal(state)
	}
}

func TestOptionalBackchannel501RetriesDescribeOnSameCameraConnection(t *testing.T) {
	camera := newFake(t)
	camera.rejectBackchannel.Store(true)
	cfg := config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("f", 40), Sources: []sourceConfig{{ID: "unv", URL: "rtsp://" + camera.server.RTSPAddress + "/camera", Paths: []string{"unv_main"}, Microphone: true}}}
	a, err := newApp(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer a.close()
	s := a.lookup("unv_main")
	eventually(t, func() bool { return s.connected.Load() == 1 && s.packets.Load() > 0 })
	if camera.maximum.Load() != 1 || s.attempts.Load() != 1 || camera.describeCalls.Load() != 2 {
		t.Fatal("optional backchannel fallback opened/restarted a camera connection")
	}
	state := s.connection.snapshot()
	if state["state"] != "connected" || state["backchannel_requested"] != false || state["last_error"] != nil {
		t.Fatal(state)
	}
	if s.status()["microphone_supported"] != false {
		t.Fatal("rejected microphone reported as available")
	}
}
