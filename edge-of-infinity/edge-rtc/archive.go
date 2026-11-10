package main

import (
	"encoding/binary"
	"errors"
	"log"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

var archiveName = regexp.MustCompile(`^(?:[0-9]{8}-[0-9]{6}|[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}(?:-[0-9]{1,9})?)\.mp4$`)
var archiveClipName = regexp.MustCompile(`^event-[1-9][0-9]{0,18}\.mp4$`)

const archiveBatchPackets = 64
const archiveBatchBytes = 2 * 1024 * 1024

type archiveCommand struct {
	Type  string  `json:"type"`
	Epoch uint32  `json:"epoch"`
	File  string  `json:"file"`
	At    float64 `json:"at"`
	Count int     `json:"count"`
	Bytes int     `json:"bytes,omitempty"`
}

func validArchiveSeek(command archiveCommand) bool {
	return validArchiveControl(command) && archiveName.MatchString(command.File)
}

func validArchiveControl(command archiveCommand) bool {
	return command.Epoch != 0 && !math.IsNaN(command.At) && !math.IsInf(command.At, 0) && command.At >= 0 && command.At <= 172800 && command.Count >= 0 && command.Count <= archiveBatchPackets && (command.Bytes == 0 || command.Bytes >= 65536 && command.Bytes <= archiveBatchBytes)
}

func (a *app) archive(w http.ResponseWriter, r *http.Request, key string) {
	if key == "__alerts" {
		reply(w, 404, map[string]string{"error": "unknown_archive"})
		return
	}
	a.serveArchive(w, r, key, "")
}

func (a *app) archiveClip(w http.ResponseWriter, r *http.Request, id string) {
	ident, err := strconv.ParseUint(id, 10, 63)
	if err != nil || ident == 0 || strconv.FormatUint(ident, 10) != id {
		reply(w, 400, map[string]string{"error": "invalid_clip"})
		return
	}
	a.serveArchive(w, r, "__alerts", "event-"+id+".mp4")
}

func (a *app) serveArchive(w http.ResponseWriter, r *http.Request, key, fixedFile string) {
	a.mu.RLock()
	root := a.archiveRoot
	directory := a.archiveKeys[key]
	a.mu.RUnlock()
	validSeek := func(command archiveCommand) bool {
		if fixedFile != "" {
			return validArchiveControl(command) && command.File == fixedFile
		}
		return validArchiveSeek(command)
	}
	if root == "" || directory == "" || !validID.MatchString(key) {
		reply(w, 404, map[string]string{"error": "unknown_archive"})
		return
	}
	select {
	case a.archiveSlots <- struct{}{}:
		defer func() { <-a.archiveSlots }()
	default:
		reply(w, 429, map[string]string{"error": "archive_viewer_limit"})
		return
	}
	ws, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	defer ws.Close()
	done := make(chan struct{})
	defer close(done)
	go func() {
		ticker := time.NewTicker(20 * time.Second)
		defer ticker.Stop()
		for {
			select {
			case <-done:
				return
			case <-a.stop:
				ws.Close()
				return
			case <-ticker.C:
				if ws.WriteControl(websocket.PingMessage, nil, time.Now().Add(2*time.Second)) != nil {
					ws.Close()
					return
				}
			}
		}
	}()
	ws.SetReadLimit(4096)
	ws.SetReadDeadline(time.Now().Add(65 * time.Second))
	ws.SetPongHandler(func(string) error { return ws.SetReadDeadline(time.Now().Add(65 * time.Second)) })
	write := func(value any) error {
		ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
		return ws.WriteJSON(value)
	}
	if write(map[string]any{"type": "archive_ready", "protocol": "edgertc/archive/1", "batch_packets": archiveBatchPackets, "batch_bytes": archiveBatchBytes, "byte_budget": true, "initial_batch": true, "pipeline_batches": 2}) != nil {
		return
	}
	var file *os.File
	defer func() {
		if file != nil {
			file.Close()
		}
	}()
	var index *archiveIndex
	var signature os.FileInfo
	var name string
	var epoch uint32
	var latestEpoch atomic.Uint32
	video, audio := 0, 0
	sendBatch := func(limit, byteLimit int) error {
		if byteLimit == 0 {
			byteLimit = archiveBatchBytes
		}
		started := time.Now()
		var readTime time.Duration
		bytes, packets := 0, 0
		for count := 0; count < limit; count++ {
			// A newly received seek cancels old delivery at an access-unit
			// boundary. Never queue several stale batches ahead of the cursor.
			if latestEpoch.Load() != epoch {
				return nil
			}
			var track *archiveTrack
			var sample archiveSample
			kind := byte(1)
			if video < len(index.video.samples) {
				track = index.video
				sample = track.samples[video]
			}
			if index.audio != nil && audio < len(index.audio.samples) && (track == nil || index.audio.samples[audio].dts < sample.dts) {
				track = index.audio
				sample = track.samples[audio]
				kind = 2
			}
			if track == nil {
				break
			}
			readStarted := time.Now()
			payload, e := archivePayload(file, track, sample)
			readTime += time.Since(readStarted)
			if e != nil {
				index = nil
				return write(map[string]any{"type": "archive_error", "epoch": epoch, "code": "archive_read_failed"})
			}
			packet := make([]byte, 14+len(payload))
			packet[0] = kind
			if sample.key {
				packet[1] = 1
			}
			binary.BigEndian.PutUint64(packet[2:10], uint64(sample.pts))
			binary.BigEndian.PutUint32(packet[10:14], epoch)
			copy(packet[14:], payload)
			ws.SetWriteDeadline(time.Now().Add(5 * time.Second))
			if e = ws.WriteMessage(websocket.BinaryMessage, packet); e != nil {
				return e
			}
			if kind == 1 {
				video++
			} else {
				audio++
			}
			bytes += len(packet)
			packets++
			// A single access unit may exceed the soft batch budget, but keeps
			// the existing per-packet limit. Never split an encoded picture.
			if bytes >= byteLimit {
				break
			}
		}
		end := video >= len(index.video.samples) && (index.audio == nil || audio >= len(index.audio.samples))
		return write(map[string]any{"type": "batch", "epoch": epoch, "end": end, "bytes": bytes, "packets": packets, "read_ms": float64(readTime.Microseconds()) / 1000, "server_ms": float64(time.Since(started).Microseconds()) / 1000})
	}
	commands := make(chan archiveCommand, 32)
	go func() {
		defer close(commands)
		defer ws.Close()
		window, count := time.Now(), 0
		for {
			var command archiveCommand
			if ws.ReadJSON(&command) != nil {
				return
			}
			ws.SetReadDeadline(time.Now().Add(65 * time.Second))
			if time.Since(window) > time.Second {
				window, count = time.Now(), 0
			}
			count++
			if count > 240 {
				return
			}
			if command.Type == "seek" && validSeek(command) {
				latestEpoch.Store(command.Epoch)
				// Coalesce queued seeks/pulls. Only the most recent requested
				// position should spend disk/network/decoder work.
				for len(commands) > 0 {
					select {
					case <-commands:
					default:
					}
				}
			}
			select {
			case commands <- command:
			case <-done:
				return
			}
		}
	}()
	for command := range commands {
		switch command.Type {
		case "seek":
			if !validSeek(command) {
				if write(map[string]any{"type": "archive_error", "epoch": command.Epoch, "code": "invalid_archive_seek"}) != nil {
					return
				}
				continue
			}
			if command.Epoch != latestEpoch.Load() {
				continue
			}
			epoch = command.Epoch
			started := time.Now()
			hit := false
			path, e := archivePath(root, directory, command.File)
			var stat os.FileInfo
			if e == nil {
				stat, e = os.Stat(path)
			}
			if e == nil && index != nil && name == command.File && os.SameFile(signature, stat) && signature.Size() == stat.Size() && signature.ModTime() == stat.ModTime() {
				hit = true
			} else if e == nil {
				if file != nil {
					file.Close()
					file = nil
				}
				index = nil
				var confined *os.Root
				confined, e = os.OpenRoot(root)
				if e == nil {
					var folder *os.Root
					folder, e = confined.OpenRoot(directory)
					if e == nil {
						file, e = folder.Open(command.File)
						folder.Close()
					}
					confined.Close()
				}
				if e == nil {
					index, e = readArchiveIndex(file)
				}
				if e == nil {
					signature = stat
					name = command.File
				}
			}
			if e != nil || index == nil {
				index = nil
				name = ""
				signature = nil
				if file != nil {
					file.Close()
					file = nil
				}
				reason := "invalid_mp4_index"
				if e != nil {
					reason = e.Error()
					var pathError *os.PathError
					if errors.As(e, &pathError) {
						reason = pathError.Op + ": " + pathError.Err.Error()
					}
					if len(reason) > 160 {
						reason = reason[:160]
					}
				}
				log.Printf("archive index unavailable key=%s file=%s reason=%s", key, command.File, reason)
				if write(map[string]any{"type": "archive_error", "epoch": epoch, "code": "archive_index_unavailable"}) != nil {
					return
				}
				continue
			}
			target := min(int64(command.At*1000000), max(0, index.duration-1))
			video, audio = index.seek(target)
			fps := float64(1000000) / float64(index.video.samples[video].duration)
			var sound map[string]any
			if index.audio != nil {
				sound = index.audio.codec
			}
			if write(map[string]any{"type": "hello", "protocol": "edgertc/archive/1", "epoch": epoch, "video": index.video.codec, "audio": sound, "target_us": target, "duration_us": index.duration, "fps": fps, "keyframe_us": index.video.samples[video].pts, "keyframe_interval_seconds": float64(index.gop) / 1000000, "index_hit": hit, "index_ms": float64(time.Since(started).Microseconds()) / 1000, "microphone": map[string]bool{"supported": false}}) != nil {
				return
			}
			log.Printf("archive seek key=%s epoch=%d target_ms=%d keyframe_ms=%d index_hit=%t index_ms=%d", key, epoch, target/1000, index.video.samples[video].pts/1000, hit, time.Since(started).Milliseconds())
			// Opt-in initial delivery removes a full request round trip after
			// every seek. Legacy clients leave Count at zero and still pull.
			if command.Count > 0 {
				if sendBatch(command.Count, command.Bytes) != nil {
					return
				}
			}
		case "pull":
			if index == nil || command.Epoch != epoch || command.Epoch != latestEpoch.Load() {
				continue
			}
			if command.Count < 1 || !validArchiveControl(command) {
				return
			}
			if sendBatch(command.Count, command.Bytes) != nil {
				return
			}
		default:
			return
		}
	}
}

func archivePath(root, key, name string) (string, error) {
	if !validID.MatchString(key) || !(archiveName.MatchString(name) || archiveClipName.MatchString(name)) {
		return "", errors.New("invalid_archive_path")
	}
	canonical, e := filepath.EvalSymlinks(root)
	if e != nil {
		return "", e
	}
	directory := filepath.Join(canonical, key)
	resolved, e := filepath.EvalSymlinks(directory)
	if e != nil || resolved != directory {
		return "", errors.New("invalid_archive_directory")
	}
	path := filepath.Join(directory, name)
	info, e := os.Lstat(path)
	if e != nil {
		return "", e
	}
	if !info.Mode().IsRegular() {
		return "", errors.New("invalid_archive_file")
	}
	return path, nil
}
