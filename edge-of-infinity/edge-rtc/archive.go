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
	"time"

	"github.com/gorilla/websocket"
)

var archiveName = regexp.MustCompile(`^(?:[0-9]{8}-[0-9]{6}|[0-9]{4}-[0-9]{2}-[0-9]{2}_[0-9]{2}-[0-9]{2}-[0-9]{2}(?:-[0-9]{1,9})?)\.mp4$`)

type archiveCommand struct {
	Type  string  `json:"type"`
	Epoch uint32  `json:"epoch"`
	File  string  `json:"file"`
	At    float64 `json:"at"`
	Count int     `json:"count"`
}

func (a *app) archive(w http.ResponseWriter, r *http.Request, key string) {
	a.mu.RLock()
	root := a.archiveRoot
	directory := a.archiveKeys[key]
	a.mu.RUnlock()
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
	if write(map[string]any{"type": "archive_ready", "protocol": "edgertc/archive/1", "batch_packets": 16, "batch_bytes": 2 * 1024 * 1024}) != nil {
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
	video, audio := 0, 0
	window := time.Now()
	commands := 0
	for {
		var command archiveCommand
		if ws.ReadJSON(&command) != nil {
			return
		}
		ws.SetReadDeadline(time.Now().Add(65 * time.Second))
		if time.Since(window) > time.Second {
			window = time.Now()
			commands = 0
		}
		commands++
		if commands > 240 {
			return
		}
		switch command.Type {
		case "seek":
			if command.Epoch == 0 || math.IsNaN(command.At) || math.IsInf(command.At, 0) || command.At < 0 || command.At > 172800 || !archiveName.MatchString(command.File) {
				if write(map[string]any{"type": "archive_error", "epoch": command.Epoch, "code": "invalid_archive_seek"}) != nil {
					return
				}
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
				if write(map[string]any{"type": "archive_error", "epoch": epoch, "code": "archive_requires_mp4"}) != nil {
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
		case "pull":
			if index == nil || command.Epoch != epoch {
				continue
			}
			if command.Count < 1 || command.Count > 16 {
				return
			}
			bytes := 0
			for count := 0; count < command.Count; count++ {
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
				payload, e := archivePayload(file, track, sample)
				if e != nil {
					if write(map[string]any{"type": "archive_error", "epoch": epoch, "code": "archive_read_failed"}) != nil {
						return
					}
					index = nil
					break
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
				if ws.WriteMessage(websocket.BinaryMessage, packet) != nil {
					return
				}
				if kind == 1 {
					video++
				} else {
					audio++
				}
				bytes += len(packet)
				if bytes >= 2*1024*1024 {
					break
				}
			}
			if index == nil {
				continue
			}
			end := video >= len(index.video.samples) && (index.audio == nil || audio >= len(index.audio.samples))
			if write(map[string]any{"type": "batch", "epoch": epoch, "end": end, "bytes": bytes}) != nil {
				return
			}
		default:
			return
		}
	}
}

func archivePath(root, key, name string) (string, error) {
	if !validID.MatchString(key) || !archiveName.MatchString(name) {
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
