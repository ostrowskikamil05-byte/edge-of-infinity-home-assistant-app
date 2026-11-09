package main

import (
	"bytes"
	"encoding/binary"
	"encoding/hex"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/gortsplib/v5/pkg/sdp"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/mpeg4audio"
	"github.com/pion/rtp"
)

func unvTestDescription(t *testing.T) *description.Session {
	t.Helper()
	sps, _ := hex.DecodeString(testHEVCSPS)
	vps, _ := hex.DecodeString("40010c01ffff016000000300900000030000030078999809")
	pps, _ := hex.DecodeString("4401c172b46240")
	var asc mpeg4audio.AudioSpecificConfig
	if err := asc.Unmarshal([]byte{0x11, 0x88}); err != nil {
		t.Fatal(err)
	}
	return &description.Session{Medias: []*description.Media{
		{Type: description.MediaTypeVideo, Control: "trackID=0", Formats: []format.Format{&format.H265{PayloadTyp: 108, VPS: vps, SPS: sps, PPS: pps}}},
		{Type: description.MediaTypeAudio, Control: "trackID=1", Formats: []format.Format{&format.MPEG4Audio{PayloadTyp: 104, Config: &asc, SizeLength: 13, IndexLength: 3, IndexDeltaLength: 3}}},
		{Type: description.MediaTypeAudio, Control: "trackID=2", IsBackChannel: true, Formats: []format.Format{
			&format.G711{PayloadTyp: 8, SampleRate: 8000, ChannelCount: 1},
			&format.G711{PayloadTyp: 0, MULaw: true, SampleRate: 8000, ChannelCount: 1},
		}},
	}}
}

// Match the reported declarations: H265/108, valid AAC/104 at 48kHz, and
// optional talk PCMA/8 + PCMU/0 + AAC/104 at 8kHz without its config.
func withBrokenTalkAAC(t *testing.T, body []byte) []byte {
	t.Helper()
	var session sdp.SessionDescription
	if err := session.Unmarshal(body); err != nil {
		t.Fatal(err)
	}
	for _, media := range session.MediaDescriptions {
		for _, attr := range media.Attributes {
			if attr.Key == "sendonly" {
				media.MediaName.Formats = append(media.MediaName.Formats, "104")
				entry := attr
				entry.Key, entry.Value = "rtpmap", "104 mpeg4-generic/8000/1"
				media.Attributes = append(media.Attributes, entry)
				entry.Key, entry.Value = "fmtp", "104 streamtype=5;mode=AAC-hbr;sizelength=13;indexlength=3;indexdeltalength=3"
				media.Attributes = append(media.Attributes, entry)
				break
			}
		}
	}
	result, err := session.Marshal()
	if err != nil {
		t.Fatal(err)
	}
	return result
}

func TestUNVMissingBackchannelAACConfigPreservesForwardAndG711(t *testing.T) {
	desc := unvTestDescription(t)
	valid, _ := desc.Marshal()
	broken := withBrokenTalkAAC(t, valid)
	var session sdp.SessionDescription
	session.Unmarshal(broken)
	var before description.Session
	if err := before.Unmarshal(&session); err == nil || !strings.Contains(err.Error(), "media 3 is invalid: config is missing") {
		t.Fatalf("fixture does not reproduce the reported failure: %v", err)
	}
	fixed, changes := filterInvalidBackchannelAAC(broken)
	if len(changes) != 1 || changes[0]["media_index"] != 3 || changes[0]["payload_type"] != 104 {
		t.Fatal(changes)
	}
	session = sdp.SessionDescription{}
	if err := session.Unmarshal(fixed); err != nil {
		t.Fatal(err)
	}
	var after description.Session
	if err := after.Unmarshal(&session); err != nil {
		t.Fatal(err)
	}
	if len(after.Medias) != 3 || len(after.Medias[2].Formats) != 2 || !after.Medias[2].IsBackChannel {
		t.Fatal("valid talk alternatives lost")
	}
	// Compare the full SDP structures, including codec parameters and control
	// URIs, of both forward media. Duplicate PT104 belongs to separate tracks.
	var original sdp.SessionDescription
	original.Unmarshal(broken)
	for i := 0; i < 2; i++ {
		if !reflect.DeepEqual(original.MediaDescriptions[i], session.MediaDescriptions[i]) {
			t.Fatalf("forward media %d changed", i)
		}
	}
	aac := after.Medias[1].Formats[0].(*format.MPEG4Audio)
	asc, _ := aac.Config.Marshal()
	if !bytes.Equal(asc, []byte{0x11, 0x88}) || aac.ClockRate() != 48000 || aac.Config.ChannelCount != 1 {
		t.Fatal("source AAC was guessed or changed")
	}
	rows := sdpFormatOverview(broken)
	if rows[1]["audio_specific_config_hex"] != "1188" || rows[4]["audio_specific_config_hex"] != nil || rows[4]["media_index"] != 3 || rows[4]["direction"] != "sendonly" {
		t.Fatal("diagnostics mixed forward AAC config into talk track", rows)
	}
}

func TestSDPCompatibilityDoesNotHideForwardOrUnrelatedErrors(t *testing.T) {
	valid, _ := unvTestDescription(t).Marshal()
	broken := withBrokenTalkAAC(t, valid)
	for name, body := range map[string][]byte{
		"valid":               valid,
		"broken_forward":      []byte(strings.ReplaceAll(string(broken), "config=1188", "unused=1188")),
		"not_backchannel":     []byte(strings.ReplaceAll(string(broken), "a=sendonly", "a=recvonly")),
		"ambiguous_direction": []byte(strings.ReplaceAll(string(broken), "a=sendonly", "a=sendonly\r\na=sendrecv")),
		"syntax":              []byte("invalid SDP"),
		"oversized":           bytes.Repeat([]byte("x"), 65537),
	} {
		t.Run(name, func(t *testing.T) {
			got, changes := filterInvalidBackchannelAAC(body)
			if len(changes) != 0 || !bytes.Equal(got, body) {
				t.Fatal("unrelated/valid description was changed", changes)
			}
		})
	}
	// A fully valid AAC talk format is kept, even though Edge currently uses
	// only the available G711 microphone alternative.
	validTalk := []byte(strings.Replace(string(broken), "104 streamtype=5;mode=", "104 config=1588;streamtype=5;mode=", 1))
	got, changes := filterInvalidBackchannelAAC(validTalk)
	if len(changes) != 0 || !bytes.Equal(got, validTalk) {
		t.Fatal("valid optional AAC was changed")
	}
}

func TestSDPCompatibilityOmitsOnlyEmptyOptionalTalkMedia(t *testing.T) {
	valid, _ := unvTestDescription(t).Marshal()
	var session sdp.SessionDescription
	if err := session.Unmarshal(withBrokenTalkAAC(t, valid)); err != nil {
		t.Fatal(err)
	}
	session.MediaDescriptions[2].MediaName.Formats = []string{"104"}
	broken, _ := session.Marshal()
	fixed, changes := filterInvalidBackchannelAAC(broken)
	var parsed sdp.SessionDescription
	if err := parsed.Unmarshal(fixed); err != nil {
		t.Fatal(err)
	}
	var desc description.Session
	if err := desc.Unmarshal(&parsed); err != nil {
		t.Fatal(err)
	}
	if len(changes) != 1 || len(desc.Medias) != 2 || desc.Medias[0].IsBackChannel || desc.Medias[1].IsBackChannel || desc.Medias[1].Formats[0].(*format.MPEG4Audio).ClockRate() != 48000 {
		t.Fatal("empty optional track blocked or changed forward media", changes)
	}
}

type unvSDPCamera struct {
	*fakeCamera
	t *testing.T
}

func (c *unvSDPCamera) OnResponse(_ *gortsplib.ServerConn, response *base.Response) {
	if response.StatusCode == base.StatusOK && len(response.Body) > 0 && len(response.Header["Content-Type"]) == 1 && response.Header["Content-Type"][0] == "application/sdp" {
		response.Body = withBrokenTalkAAC(c.t, response.Body)
	}
}

func TestUNVMalformedTalkAACConnectsLiveRecorderAndMicOnOneUpstream(t *testing.T) {
	desc := unvTestDescription(t)
	camera := &unvSDPCamera{fakeCamera: &fakeCamera{}, t: t}
	camera.server = &gortsplib.Server{RTSPAddress: freeAddress(), Handler: camera}
	if err := camera.server.Start(); err != nil {
		t.Fatal(err)
	}
	camera.stream = &gortsplib.ServerStream{Server: camera.server, Desc: desc}
	if err := camera.stream.Initialize(); err != nil {
		camera.server.Close()
		t.Fatal(err)
	}
	t.Cleanup(func() { camera.server.Close(); camera.stream.Close() })
	a, err := newApp(config{RTSP: freeAddress(), HTTP: freeAddress(), Token: strings.Repeat("u", 40), Sources: []sourceConfig{{ID: "unv", URL: "rtsp://" + camera.server.RTSPAddress + "/media/video1", Paths: []string{"front_main"}, Microphone: true}}})
	if err != nil {
		t.Fatal(err)
	}
	defer a.close()
	s := a.lookup("front_main")
	eventually(t, func() bool { return s.connected.Load() == 1 && s.connection.snapshot()["state"] == "connected" })
	state := s.status()
	if state["microphone_supported"] != true || state["video"].(map[string]any)["codec_name"] != "h265" || state["audio"].(map[string]any)["sampleRate"] != 48000 {
		t.Fatal(state)
	}
	viewer, hello, _, err := s.attach(true)
	if err != nil {
		t.Fatal(err)
	}
	defer s.detach(viewer)
	if hello["audio"].(map[string]any)["codec"] != "mp4a.40.2" {
		t.Fatal(hello)
	}
	videoPayload, audioAU := []byte{0x26, 1, 0x88, 0x84, 0x21}, []byte{0x01, 0x02, 0x03, 0x04}
	// AU-headers-length=16, AU-size=4 (13 bits), AU-index=0.
	audioPayload := append([]byte{0, 16, 0, 32}, audioAU...)
	var videoReceived, audioReceived atomic.Uint32
	relayClient(t, a, "EdgeRecorder/test", func(packet *rtp.Packet) {
		if packet.PayloadType == 108 && bytes.Equal(packet.Payload, videoPayload) {
			videoReceived.Add(1)
		}
		if packet.PayloadType == 104 && bytes.Equal(packet.Payload, audioPayload) {
			audioReceived.Add(1)
		}
	})
	for i := 1; i <= 4; i++ {
		camera.stream.WritePacketRTP(desc.Medias[0], &rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 108, Marker: true, SequenceNumber: uint16(i), Timestamp: uint32(i) * 4500, SSRC: 123}, Payload: videoPayload})
		camera.stream.WritePacketRTP(desc.Medias[1], &rtp.Packet{Header: rtp.Header{Version: 2, PayloadType: 104, Marker: true, SequenceNumber: uint16(i), Timestamp: uint32(i) * 1024, SSRC: 124}, Payload: audioPayload})
		time.Sleep(10 * time.Millisecond)
	}
	eventually(t, func() bool { return videoReceived.Load() == 4 && audioReceived.Load() == 4 })
	seenVideo, seenAAC := false, false
	deadline := time.After(3 * time.Second)
	for !seenVideo || !seenAAC {
		select {
		case packet := <-viewer.queue:
			viewer.consumed(packet)
			seenVideo = seenVideo || packet[0] == 1 && bytes.Contains(packet[10:], videoPayload)
			seenAAC = seenAAC || packet[0] == 2 && bytes.Equal(packet[10:], audioAU)
		case <-deadline:
			t.Fatal("live viewer did not receive source HEVC and AAC")
		}
	}
	pcm := make([]byte, 320)
	for i := 0; i < len(pcm); i += 2 {
		binary.LittleEndian.PutUint16(pcm[i:], 1000)
	}
	if err := s.writePCM(viewer, pcm); err != nil {
		t.Fatal(err)
	}
	eventually(t, func() bool { return camera.mic.Load() == 1 })
	if camera.maximum.Load() != 1 || camera.active.Load() != 1 || camera.describeCalls.Load() != 1 || s.attempts.Load() != 1 {
		t.Fatal("SDP compatibility opened another camera connection")
	}
	connection := s.connection.snapshot()
	if connection["last_error"] != nil || len(connection["optional_audio_filtered"].([]map[string]any)) != 1 {
		t.Fatal(connection)
	}
}
