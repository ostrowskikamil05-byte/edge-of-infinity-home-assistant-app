package main

import (
	"encoding/binary"
	"encoding/json"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	mp4 "github.com/abema/go-mp4"
	"github.com/gorilla/websocket"
)

func TestArchiveSampleTables(t *testing.T) {
	track := &archiveTrack{scale: 1000, stsz: &mp4.Stsz{SampleCount: 4, SampleSize: 10}, stts: &mp4.Stts{Entries: []mp4.SttsEntry{{SampleCount: 4, SampleDelta: 50}}},
		ctts: &mp4.Ctts{Entries: []mp4.CttsEntry{{SampleCount: 1, SampleOffsetV0: 100}, {SampleCount: 1, SampleOffsetV0: 150}, {SampleCount: 2, SampleOffsetV0: 0}}},
		stss: &mp4.Stss{SampleNumber: []uint32{1, 3}}, stsc: &mp4.Stsc{Entries: []mp4.StscEntry{{FirstChunk: 1, SamplesPerChunk: 2, SampleDescriptionIndex: 1}}}, chunks: []uint64{20, 200},
		edits: &mp4.Elst{Entries: []mp4.ElstEntry{{SegmentDurationV0: 100, MediaTimeV0: -1, MediaRateInteger: 1}, {SegmentDurationV0: 200, MediaTimeV0: 100, MediaRateInteger: 1}}}}
	if err := track.buildSamples(1000, 240); err != nil {
		t.Fatal(err)
	}
	if track.samples[0].pts != 100000 || track.samples[1].pts != 200000 || track.samples[2].pts != 100000 || track.samples[2].offset != 200 || !track.samples[2].key || track.samples[1].key {
		t.Fatalf("wrong sample addressing/reordering: %+v", track.samples)
	}
	if _, err := archivePath(t.TempDir(), "../other", "20261109-000000.mp4"); err == nil {
		t.Fatal("traversal accepted")
	}
	root := t.TempDir()
	outside := t.TempDir()
	os.Symlink(outside, filepath.Join(root, "front_0"))
	if _, err := archivePath(root, "front_0", "20261109-000000.mp4"); err == nil {
		t.Fatal("external symlink accepted")
	}
}

func TestArchiveFromFFmpeg(t *testing.T) {
	ffmpeg, err := exec.LookPath("ffmpeg")
	if err != nil {
		t.Skip("FFmpeg fixture tool is unavailable")
	}
	for _, codec := range []string{"h264", "hevc"} {
		t.Run(codec, func(t *testing.T) {
			root := t.TempDir()
			directory := filepath.Join(root, "front_0")
			if err = os.Mkdir(directory, 0700); err != nil {
				t.Fatal(err)
			}
			name := "20261109-000000.mp4"
			path := filepath.Join(directory, name)
			video := []string{"-c:v", "libx264", "-threads", "1", "-g", "40", "-sc_threshold", "0"}
			if codec == "hevc" {
				video = []string{"-c:v", "libx265", "-x265-params", "pools=none:frame-threads=1:keyint=40:min-keyint=40:scenecut=0:log-level=error", "-tag:v", "hev1"}
			}
			args := []string{"-v", "error", "-f", "lavfi", "-i", "testsrc2=size=320x180:rate=20", "-f", "lavfi", "-i", "sine=sample_rate=16000", "-t", "6"}
			args = append(args, video...)
			args = append(args, "-c:a", "aac", "-threads:a", "1", "-b:a", "32k", "-output_ts_offset", "0.1", "-movflags", "+faststart", path)
			if out, e := exec.Command(ffmpeg, args...).CombinedOutput(); e != nil {
				t.Fatalf("FFmpeg: %v %s", e, out)
			}
			f, e := os.Open(path)
			if e != nil {
				t.Fatal(e)
			}
			index, e := readArchiveIndex(f)
			if e != nil {
				t.Fatal(e)
			}
			defer f.Close()
			if index.audio == nil || len(index.keys) != 3 || index.gop != 2000000 || index.duration < 5900000 || index.duration > 6300000 {
				t.Fatalf("wrong archive metadata: %+v", index)
			}
			v, _ := index.seek(4900000)
			if !index.video.samples[v].key || index.video.samples[v].pts > 4900000 || index.video.samples[v].pts < 3900000 {
				t.Fatal("seek did not choose preceding sync sample")
			}
			payload, e := archivePayload(f, index.video, index.video.samples[v])
			if e != nil || len(payload) < 8 || codec == "hevc" && binary.BigEndian.Uint32(payload[:4]) != 1 || codec == "h264" && index.video.codec["format"] != "avc" {
				t.Fatalf("invalid Annex-B %v", e)
			}
			a, e := newApp(config{HTTP: "127.0.0.1:0", RTSP: "127.0.0.1:0", Token: strings.Repeat("a", 48), Recordings: root, Archives: map[string]string{"front_0": "front_0"}})
			if e != nil {
				t.Fatal(e)
			}
			defer a.close()
			headers := http.Header{"Authorization": []string{"Bearer " + a.cfg.Token}}
			ws, _, e := websocket.DefaultDialer.Dial("ws://"+a.listener.Addr().String()+"/api/archive/front_0", headers)
			if e != nil {
				t.Fatal(e)
			}
			defer ws.Close()
			ws.SetReadDeadline(time.Now().Add(5 * time.Second))
			var hello map[string]any
			if e = ws.ReadJSON(&hello); e != nil {
				t.Fatal(e)
			}
			if hello["protocol"] != "edgertc/archive/1" || hello["batch_packets"] != float64(archiveBatchPackets) || hello["initial_batch"] != true || hello["pipeline_batches"] != float64(2) {
				t.Fatal(hello)
			}
			for epoch := uint32(1); epoch <= 2; epoch++ {
				ws.WriteJSON(archiveCommand{Type: "seek", Epoch: epoch, File: name, At: 4.9})
				if e = ws.ReadJSON(&hello); e != nil {
					t.Fatal(e)
				}
				if hello["type"] != "hello" || hello["epoch"] != float64(epoch) || hello["index_hit"] != (epoch == 2) {
					t.Fatal(hello)
				}
				ws.WriteJSON(archiveCommand{Type: "pull", Epoch: epoch, Count: 8})
				packets := 0
				for {
					kind, data, e := ws.ReadMessage()
					if e != nil {
						t.Fatal(e)
					}
					if kind == websocket.TextMessage {
						var batch map[string]any
						json.Unmarshal(data, &batch)
						if batch["type"] != "batch" {
							t.Fatal(batch)
						}
						break
					}
					if binary.BigEndian.Uint32(data[10:14]) != epoch {
						t.Fatal("wrong request epoch")
					}
					if packets == 0 && (data[0] != 1 || data[1]&1 == 0) {
						t.Fatal("first packet is not a video keyframe")
					}
					packets++
				}
				if packets < 1 || packets > 8 {
					t.Fatal("unbounded batch", packets)
				}
			}
			if len(a.sources) != 0 {
				t.Fatal("archive opened a camera source")
			}
			ws.WriteJSON(archiveCommand{Type: "seek", Epoch: 3, File: "../secret.mp4", At: 0})
			ws.ReadJSON(&hello)
			if hello["code"] != "invalid_archive_seek" {
				t.Fatal("path validation", hello)
			}
			// First media is included only when the seeker explicitly opts in;
			// old Count=0 clients above still require their explicit pull.
			ws.WriteJSON(archiveCommand{Type: "seek", Epoch: 4, File: name, At: .1, Count: archiveBatchPackets})
			if e = ws.ReadJSON(&hello); e != nil || hello["type"] != "hello" {
				t.Fatalf("initial seek metadata: %v %v", e, hello)
			}
			packets, bytes := 0, 0
			for {
				kind, data, e := ws.ReadMessage()
				if e != nil {
					t.Fatal(e)
				}
				if kind == websocket.TextMessage {
					var batch map[string]any
					json.Unmarshal(data, &batch)
					if batch["type"] != "batch" || batch["packets"] != float64(packets) || batch["bytes"] != float64(bytes) || packets != archiveBatchPackets {
						t.Fatalf("initial bounded batch: %v packets=%d", batch, packets)
					}
					break
				}
				if binary.BigEndian.Uint32(data[10:14]) != 4 {
					t.Fatal("initial batch epoch")
				}
				packets++
				bytes += len(data)
			}
			for epoch := uint32(5); epoch <= 7; epoch++ {
				ws.WriteJSON(archiveCommand{Type: "seek", Epoch: epoch, File: name, At: float64(epoch - 4), Count: archiveBatchPackets})
			}
			for {
				kind, data, e := ws.ReadMessage()
				if e != nil {
					t.Fatal(e)
				}
				if kind != websocket.TextMessage {
					continue
				}
				var message map[string]any
				json.Unmarshal(data, &message)
				if message["type"] == "hello" && message["epoch"] == float64(7) {
					break
				}
			}
			kind, data, e := ws.ReadMessage()
			if e != nil || kind != websocket.BinaryMessage || binary.BigEndian.Uint32(data[10:14]) != 7 || data[1]&1 == 0 {
				t.Fatalf("latest seek did not start with its keyframe: %v", e)
			}
		})
	}
}
