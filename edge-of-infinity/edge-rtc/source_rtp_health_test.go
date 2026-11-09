package main

import (
	"strings"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/gortsplib/v5/pkg/liberrors"
)

func healthDescription() *description.Session {
	return &description.Session{Medias: []*description.Media{{Type: description.MediaTypeVideo, Formats: []format.Format{&format.H264{PayloadTyp: 96, PacketizationMode: 1}}},
		{Type: description.MediaTypeAudio, Formats: []format.Format{&format.G711{PayloadTyp: 8, SampleRate: 8000, ChannelCount: 1}}}}}
}

func TestUnknownRTPDoesNotRemapOrRestartHealthyVideo(t *testing.T) {
	var health sourceRTPHealth
	start := time.Now()
	health.begin(healthDescription(), start)
	logs := 0
	for i := 0; i < 1000; i++ {
		now := start.Add(6*time.Second + time.Duration(i)*time.Millisecond)
		health.videoPacket(now)
		entry, refresh := health.decodeError(liberrors.ErrClientRTPPacketUnknownPayloadType{PayloadType: 108}, now)
		if refresh {
			t.Fatal("healthy video requested reconnect")
		}
		if entry != nil {
			logs++
			if entry["codec_remapped"] != false {
				t.Fatal("numeric payload guessed a codec")
			}
		}
	}
	state := health.snapshot(start.Add(8 * time.Second))
	if logs != 1 || state["unknown_payload_types"].(map[string]uint64)["108"] != 1000 {
		t.Fatalf("lost evidence or unbounded log: %v logs=%d", state, logs)
	}
	if state["declared_formats"].([]map[string]any)[0]["payload_type"] != uint8(96) {
		t.Fatal("SDP changed")
	}
}

func TestUnknownRTPRefreshRequiresMissingVideoAndHasCrossConnectionCooldown(t *testing.T) {
	var health sourceRTPHealth
	start := time.Now()
	health.begin(healthDescription(), start)
	err := liberrors.ErrClientRTPPacketUnknownPayloadType{PayloadType: 104}
	for i := 0; i < 20; i++ {
		if _, refresh := health.decodeError(err, start.Add(time.Second)); refresh {
			t.Fatal("startup was not allowed to warm")
		}
	}
	if _, refresh := health.decodeError(err, start.Add(6*time.Second)); !refresh {
		t.Fatal("unknown packets with no video did not refresh SDP")
	}
	health.begin(healthDescription(), start.Add(7*time.Second))
	for i := 0; i < 30; i++ {
		if _, refresh := health.decodeError(err, start.Add(14*time.Second)); refresh {
			t.Fatal("refresh cooldown was lost on reconnect")
		}
	}
	if _, refresh := health.decodeError(err, start.Add(67*time.Second)); !refresh {
		t.Fatal("persistent failure cannot retry after cooldown")
	}
}

func TestRTPDescriptionRefreshClosesOldCameraBeforeOpeningReplacement(t *testing.T) {
	camera := newFake(t)
	camera.videoPaused.Store(true)
	cfg := config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("u", 40), Sources: []sourceConfig{{ID: "front", URL: "rtsp://" + camera.server.RTSPAddress + "/camera", Paths: []string{"front_main"}}}}
	a, err := newApp(cfg)
	if err != nil {
		t.Fatal(err)
	}
	defer a.close()
	s := a.lookup("front_main")
	eventually(t, func() bool {
		return s.connected.Load() == 1 && len(s.rtpHealth.snapshot(time.Now())["declared_formats"].([]map[string]any)) > 0
	})
	s.rtpHealth.begin(healthDescription(), time.Now().Add(-6*time.Second))
	s.mu.RLock()
	client := s.client
	s.mu.RUnlock()
	for i := 0; i < 20; i++ {
		s.handleSourceDecodeError(liberrors.ErrClientRTPPacketUnknownPayloadType{PayloadType: 108}, time.Now(), client.Close)
	}
	camera.videoPaused.Store(false)
	eventually(t, func() bool { return s.attempts.Load() >= 2 && s.packets.Load() > 0 })
	if camera.maximum.Load() != 1 || s.rtpHealth.snapshot(time.Now())["description_refreshes"] != uint64(1) {
		t.Fatal("camera connections overlapped or refresh repeated")
	}
}
