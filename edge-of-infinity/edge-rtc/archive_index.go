package main

// A sample-table index, not a decoded-video cache. Payloads are read with
// ReadAt only when a viewer requests the next bounded batch.
import (
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"math"
	"os"
	"sort"

	mp4 "github.com/abema/go-mp4"
	"github.com/bluenviron/mediacommon/v2/pkg/codecs/mpeg4audio"
)

const archiveSampleLimit = 100000
const archivePacketLimit = 8 * 1024 * 1024

type archiveSample struct {
	offset             int64
	size               uint32
	dts, pts, duration int64
	key                bool
}
type archiveTrack struct {
	kind    string
	scale   uint32
	codec   map[string]any
	params  [][]byte
	nalSize int
	samples []archiveSample
	stts    *mp4.Stts
	ctts    *mp4.Ctts
	stsc    *mp4.Stsc
	stsz    *mp4.Stsz
	stss    *mp4.Stss
	chunks  []uint64
	edits   *mp4.Elst
}
type archiveIndex struct {
	video, audio *archiveTrack
	keys         []int
	duration     int64
	gop          int64
}

func readArchiveIndex(f *os.File) (*archiveIndex, error) {
	stat, err := f.Stat()
	if err != nil {
		return nil, err
	}
	var tracks []*archiveTrack
	var track *archiveTrack
	var movieScale uint32
	_, err = mp4.ReadBoxStructure(f, func(h *mp4.ReadHandle) (interface{}, error) {
		name := h.BoxInfo.Type.String()
		if name == "moof" {
			return nil, errors.New("fragmented_archive_requires_mp4")
		}
		if name == "moov" && h.BoxInfo.Size > 8*1024*1024 {
			return nil, errors.New("archive_index_limit")
		}
		switch name {
		case "moov", "mdia", "minf", "stbl", "edts", "stsd":
			return h.Expand()
		case "trak":
			if len(tracks) >= 4 {
				return nil, errors.New("archive_track_limit")
			}
			track = &archiveTrack{}
			tracks = append(tracks, track)
			return h.Expand()
		case "avc1", "avc3", "hvc1", "hev1", "mp4a", "alaw", "ulaw":
			if track == nil {
				return nil, errors.New("archive_invalid_track")
			}
			return h.Expand()
		case "mvhd", "mdhd", "hdlr", "avcC", "hvcC", "esds", "stts", "ctts", "stss", "stsz", "stsc", "stco", "co64", "elst":
		default:
			return nil, nil // pasp, btrt, udta and other optional boxes do not affect sample addressing.
		}
		if h.BoxInfo.Size > 8*1024*1024 {
			return nil, errors.New("archive_index_limit")
		}
		// Bound declared table counts before the library allocates their slices.
		switch name {
		case "stts", "ctts", "stss", "stsc", "stco", "co64", "stsz":
			var header [12]byte
			n := 8
			if name == "stsz" {
				n = 12
			}
			if _, e := f.ReadAt(header[:n], int64(h.BoxInfo.Offset+h.BoxInfo.HeaderSize)); e != nil {
				return nil, e
			}
			at := 4
			if name == "stsz" {
				at = 8
			}
			count := uint32(header[at])<<24 | uint32(header[at+1])<<16 | uint32(header[at+2])<<8 | uint32(header[at+3])
			if count > archiveSampleLimit {
				return nil, errors.New("archive_sample_limit")
			}
		}
		box, _, e := h.ReadPayload()
		if e != nil {
			return nil, e
		}
		if name == "mvhd" {
			movieScale = box.(*mp4.Mvhd).Timescale
			return nil, nil
		}
		if track == nil {
			return nil, errors.New("archive_invalid_track")
		}
		switch b := box.(type) {
		case *mp4.Mdhd:
			track.scale = b.Timescale
		case *mp4.Hdlr:
			track.kind = string(b.HandlerType[:])
		case *mp4.Elst:
			track.edits = b
		case *mp4.Stts:
			track.stts = b
		case *mp4.Ctts:
			track.ctts = b
		case *mp4.Stss:
			track.stss = b
		case *mp4.Stsz:
			track.stsz = b
		case *mp4.Stsc:
			track.stsc = b
		case *mp4.Stco:
			for _, off := range b.ChunkOffset {
				track.chunks = append(track.chunks, uint64(off))
			}
		case *mp4.Co64:
			track.chunks = b.ChunkOffset
		case *mp4.AVCDecoderConfiguration:
			if len(b.SequenceParameterSets) == 0 || len(b.PictureParameterSets) == 0 {
				return nil, errors.New("archive_h264_parameters")
			}
			sps := b.SequenceParameterSets[0].NALUnit
			if len(sps) < 4 {
				return nil, errors.New("archive_h264_parameters")
			}
			track.codec = map[string]any{"codec": fmt.Sprintf("avc1.%02X%02X%02X", sps[1], sps[2], sps[3]), "codec_name": "h264", "format": "annexb"}
			config := make([]byte, h.BoxInfo.Size-h.BoxInfo.HeaderSize)
			if _, e = f.ReadAt(config, int64(h.BoxInfo.Offset+h.BoxInfo.HeaderSize)); e != nil {
				return nil, e
			}
			track.codec["format"] = "avc"
			track.codec["config"] = base64.StdEncoding.EncodeToString(config)
			track.params = [][]byte{sps, b.PictureParameterSets[0].NALUnit}
			track.nalSize = int(b.LengthSizeMinusOne) + 1
		case *mp4.HvcC:
			var vps, sps, pps []byte
			for _, array := range b.NaluArrays {
				if len(array.Nalus) == 0 {
					continue
				}
				switch array.NaluType {
				case 32:
					vps = array.Nalus[0].NALUnit
				case 33:
					sps = array.Nalus[0].NALUnit
				case 34:
					pps = array.Nalus[0].NALUnit
				}
			}
			codec, e := hevcCodec(sps)
			if e != nil || len(vps) == 0 || len(pps) == 0 {
				return nil, errors.New("archive_hevc_parameters")
			}
			track.codec = map[string]any{"codec": codec, "codec_name": "h265", "format": "annexb"}
			track.params = [][]byte{vps, sps, pps}
			track.nalSize = int(b.LengthSizeMinusOne) + 1
		case *mp4.Esds:
			for _, descriptor := range b.Descriptors {
				if descriptor.Tag != mp4.DecSpecificInfoTag {
					continue
				}
				var asc mpeg4audio.AudioSpecificConfig
				if asc.Unmarshal(descriptor.Data) != nil {
					continue
				}
				track.codec = map[string]any{"codec": fmt.Sprintf("mp4a.40.%d", asc.Type), "sampleRate": asc.SampleRate, "channels": asc.ChannelCount, "config": base64.StdEncoding.EncodeToString(descriptor.Data)}
			}
		}
		return nil, nil
	})
	if err != nil {
		return nil, err
	}
	index := &archiveIndex{}
	for _, t := range tracks {
		if t.kind != "vide" && t.kind != "soun" {
			continue
		}
		if t.codec == nil {
			return nil, errors.New("archive_codec_requires_mp4")
		}
		if err = t.buildSamples(movieScale, stat.Size()); err != nil {
			return nil, err
		}
		if t.kind == "vide" && index.video == nil {
			index.video = t
		}
		if t.kind == "soun" && index.audio == nil {
			index.audio = t
		}
	}
	if index.video == nil || len(index.video.samples) == 0 {
		return nil, errors.New("archive_no_video")
	}
	var lastKey int64
	for i, s := range index.video.samples {
		if s.key {
			if len(index.keys) > 0 {
				index.gop = max(index.gop, s.pts-lastKey)
			}
			lastKey = s.pts
			index.keys = append(index.keys, i)
		}
		index.duration = max(index.duration, s.pts+s.duration)
	}
	if len(index.keys) == 0 {
		return nil, errors.New("archive_no_keyframe")
	}
	if len(index.keys) == 1 {
		index.gop = index.duration
	}
	return index, nil
}

func (t *archiveTrack) buildSamples(movieScale uint32, fileSize int64) error {
	if t.scale == 0 || t.stsz == nil || t.stts == nil || t.stsc == nil || len(t.stsc.Entries) == 0 || t.stsc.Entries[0].FirstChunk != 1 {
		return errors.New("archive_missing_sample_table")
	}
	n := int(t.stsz.SampleCount)
	if n == 0 || n > archiveSampleLimit {
		return errors.New("archive_sample_limit")
	}
	if t.stsz.SampleSize == 0 && len(t.stsz.EntrySize) != n {
		return errors.New("archive_sample_sizes")
	}
	t.samples = make([]archiveSample, n)
	shift := int64(0)
	if t.edits != nil {
		if len(t.edits.Entries) > 2 {
			return errors.New("archive_complex_edit")
		}
		for _, entry := range t.edits.Entries {
			if entry.MediaRateInteger != 1 || entry.MediaRateFraction != 0 {
				return errors.New("archive_edit_rate")
			}
			media, length := int64(entry.MediaTimeV0), int64(entry.SegmentDurationV0)
			if t.edits.GetVersion() == 1 {
				media, length = entry.MediaTimeV1, int64(entry.SegmentDurationV1)
			}
			if media == -1 {
				if movieScale == 0 {
					return errors.New("archive_movie_scale")
				}
				shift += length * 1000000 / int64(movieScale)
			} else if media >= 0 {
				shift -= media * 1000000 / int64(t.scale)
			} else {
				return errors.New("archive_edit_time")
			}
		}
	}
	i := 0
	ticks := int64(0)
	for _, run := range t.stts.Entries {
		if run.SampleDelta == 0 || uint64(i)+uint64(run.SampleCount) > uint64(n) {
			return errors.New("archive_invalid_clock")
		}
		for range run.SampleCount {
			s := &t.samples[i]
			s.dts = ticks*1000000/int64(t.scale) + shift
			s.pts = s.dts
			s.duration = int64(run.SampleDelta) * 1000000 / int64(t.scale)
			s.key = t.stss == nil
			ticks += int64(run.SampleDelta)
			i++
		}
	}
	if i != n {
		return errors.New("archive_sample_clock_count")
	}
	if t.ctts != nil {
		i = 0
		for _, run := range t.ctts.Entries {
			if uint64(i)+uint64(run.SampleCount) > uint64(n) {
				return errors.New("archive_invalid_composition")
			}
			delta := int64(run.SampleOffsetV0)
			if t.ctts.GetVersion() == 1 {
				delta = int64(run.SampleOffsetV1)
			}
			for range run.SampleCount {
				t.samples[i].pts += delta * 1000000 / int64(t.scale)
				i++
			}
		}
		if i != n {
			return errors.New("archive_composition_count")
		}
	}
	if t.stss != nil {
		for _, number := range t.stss.SampleNumber {
			if number == 0 || number > uint32(n) {
				return errors.New("archive_sync_sample")
			}
			t.samples[number-1].key = true
		}
	}
	i = 0
	run := 0
	for chunk, position := range t.chunks {
		for run+1 < len(t.stsc.Entries) && int(t.stsc.Entries[run+1].FirstChunk) <= chunk+1 {
			run++
		}
		entry := t.stsc.Entries[run]
		if entry.SampleDescriptionIndex != 1 || entry.SamplesPerChunk == 0 || uint64(i)+uint64(entry.SamplesPerChunk) > uint64(n) {
			return errors.New("archive_chunk_table")
		}
		for range entry.SamplesPerChunk {
			s := &t.samples[i]
			s.size = t.stsz.SampleSize
			if s.size == 0 {
				s.size = t.stsz.EntrySize[i]
			}
			if s.size == 0 || s.size > archivePacketLimit || position > math.MaxInt64 || position+uint64(s.size) > uint64(fileSize) {
				return errors.New("archive_payload_bounds")
			}
			s.offset = int64(position)
			position += uint64(s.size)
			i++
		}
	}
	if i != n {
		return errors.New("archive_chunk_count")
	}
	// Release raw sample tables after building the compact index.
	t.stsz = nil
	t.stts = nil
	t.stsc = nil
	t.stss = nil
	t.ctts = nil
	t.chunks = nil
	t.edits = nil
	return nil
}

func (index *archiveIndex) seek(target int64) (int, int) {
	k := sort.Search(len(index.keys), func(i int) bool { return index.video.samples[index.keys[i]].pts > target }) - 1
	if k < 0 {
		k = 0
	}
	audio := 0
	if index.audio != nil {
		audio = sort.Search(len(index.audio.samples), func(i int) bool { return index.audio.samples[i].pts >= target-20000 })
	}
	return index.keys[k], audio
}

func archivePayload(f *os.File, t *archiveTrack, s archiveSample) ([]byte, error) {
	data := make([]byte, s.size)
	if _, err := f.ReadAt(data, s.offset); err != nil {
		return nil, err
	}
	if t.kind != "vide" {
		return data, nil
	}
	if t.codec["format"] == "avc" {
		return data, nil
	} // Exact MP4 sample + avcC; Safari handles reordered H264 pictures this way.
	var out []byte
	if s.key {
		for _, param := range t.params {
			out = append(out, 0, 0, 0, 1)
			out = append(out, param...)
		}
	}
	for len(data) > 0 {
		if t.nalSize < 1 || t.nalSize > 4 || len(data) < t.nalSize {
			return nil, errors.New("archive_nal_length")
		}
		size := uint32(0)
		for _, b := range data[:t.nalSize] {
			size = size<<8 | uint32(b)
		}
		data = data[t.nalSize:]
		if size == 0 || uint64(size) > uint64(len(data)) {
			return nil, io.ErrUnexpectedEOF
		}
		out = append(out, 0, 0, 0, 1)
		out = append(out, data[:size]...)
		data = data[size:]
		if len(out) > archivePacketLimit {
			return nil, errors.New("archive_access_unit_limit")
		}
	}
	return out, nil
}
