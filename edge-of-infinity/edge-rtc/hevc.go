package main

import (
	"encoding/binary"
	"errors"
	"fmt"
	"math/bits"

	"github.com/bluenviron/gortsplib/v5"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	codech265 "github.com/bluenviron/mediacommon/v2/pkg/codecs/h265"
	"github.com/pion/rtp"
)

// hevcCodec reads general_profile_tier_level from an SPS and emits RFC 6381.
func hevcCodec(sps []byte) (string, error) {
	if len(sps) < 3 || (sps[0]>>1)&63 != 33 {
		return "", errors.New("invalid HEVC SPS")
	}
	rbsp := make([]byte, 0, len(sps))
	zeros := 0
	for _, value := range sps[2:] {
		if zeros >= 2 && value == 3 {
			zeros = 0
			continue
		}
		rbsp = append(rbsp, value)
		if value == 0 {
			zeros++
		} else {
			zeros = 0
		}
	}
	if len(rbsp) < 13 {
		return "", errors.New("truncated HEVC SPS")
	}
	space := []string{"", "A", "B", "C"}[rbsp[1]>>6]
	tier := "L"
	if rbsp[1]&32 != 0 {
		tier = "H"
	}
	codec := fmt.Sprintf("hev1.%s%d.%X.%s%d", space, rbsp[1]&31, bits.Reverse32(binary.BigEndian.Uint32(rbsp[2:6])), tier, rbsp[12])
	constraints := rbsp[6:12]
	for len(constraints) > 1 && constraints[len(constraints)-1] == 0 {
		constraints = constraints[:len(constraints)-1]
	}
	for _, value := range constraints {
		codec += fmt.Sprintf(".%X", value)
	}
	return codec, nil
}

type hevcParameters struct{ vps, sps, pps []byte }

func (h *hevcParameters) accessUnit(au [][]byte) ([]byte, bool) {
	key := false
	for _, n := range au {
		if len(n) < 2 {
			continue
		}
		typ := (n[0] >> 1) & 63
		if typ >= 16 && typ <= 21 {
			key = true
		}
		switch typ {
		case 32:
			h.vps = append([]byte(nil), n...)
		case 33:
			h.sps = append([]byte(nil), n...)
		case 34:
			h.pps = append([]byte(nil), n...)
		}
	}
	var payload []byte
	appendNAL := func(n []byte) {
		if len(n) != 0 {
			payload = append(payload, 0, 0, 0, 1)
			payload = append(payload, n...)
		}
	}
	if key {
		appendNAL(h.vps)
		appendNAL(h.sps)
		appendNAL(h.pps)
	}
	for _, n := range au {
		appendNAL(n)
	}
	return payload, key
}

func (s *source) makeH265Handler(c *gortsplib.Client, media *description.Media, f *format.H265) func(*rtp.Packet) {
	decoder, err := f.CreateDecoder()
	if err != nil {
		return nil
	}
	vps, sps, pps := f.SafeParams()
	params := hevcParameters{vps, sps, pps}
	lastCodec := ""
	lastReady := false
	update := func() {
		codec, e := hevcCodec(params.sps)
		ready := len(params.vps) > 0 && len(params.sps) > 0 && len(params.pps) > 0
		if e == nil && (codec != lastCodec || ready != lastReady) {
			lastCodec = codec
			lastReady = ready
			video := map[string]any{"codec": codec, "codec_name": "h265", "format": "annexb", "parameters_ready": ready}
			var parsed codech265.SPS
			if parsed.Unmarshal(params.sps) == nil {
				video["width"] = parsed.Width()
				video["height"] = parsed.Height()
				if fps := parsed.FPS(); fps > 0 && fps <= 240 {
					video["fps"] = fps
				}
			}
			s.setVideo(video)
		}
	}
	update()
	return func(p *rtp.Packet) {
		stamp, validTimestamp := c.PacketPTS(media, p)
		au, e := decoder.Decode(p)
		if e != nil || !validTimestamp {
			return
		}
		payload, key := params.accessUnit(au)
		// Some cameras provide VPS/SPS/PPS only in-band. Publish them in the
		// shared RTSP description too, so new recorders can initialize HEVC.
		for _, nalu := range au {
			if len(nalu) > 1 && (nalu[0]>>1)&63 >= 32 && (nalu[0]>>1)&63 <= 34 && len(params.vps) > 0 && len(params.sps) > 0 && len(params.pps) > 0 {
				f.SafeSetParams(params.vps, params.sps, params.pps)
				break
			}
		}
		update()
		if lastCodec == "" || len(params.vps) == 0 || len(params.pps) == 0 {
			return
		}
		s.broadcast(frame(1, key, stamp*1000000/90000, payload), key)
	}
}
