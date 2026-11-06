package main

import (
	"bytes"
	"encoding/hex"
	"testing"
)

// Generated with FFmpeg/libx265 from testsrc2, 320x180, Main profile.
const testHEVCSPS = "42010101600000030090000003000003003ca00a080c1f3e5ba4a4c2f016808000000300800000030284"

func TestHEVCRFC6381AndAnnexB(t *testing.T) {
	sps, _ := hex.DecodeString(testHEVCSPS)
	codec, err := hevcCodec(sps)
	if err != nil || codec != "hev1.1.6.L60.90" {
		t.Fatalf("wrong codec: %q %v", codec, err)
	}
	p := hevcParameters{}
	vps, pps, cra := []byte{64, 1, 2}, []byte{68, 1, 3}, []byte{42, 1, 4}
	payload, key := p.accessUnit([][]byte{vps, sps, pps, cra})
	if !key || !bytes.Contains(payload, append([]byte{0, 0, 0, 1}, sps...)) {
		t.Fatal("missing parameter sets/keyframe")
	}
	payload, key = p.accessUnit([][]byte{cra})
	if !key || !bytes.HasPrefix(payload, append([]byte{0, 0, 0, 1}, vps...)) || !bytes.Contains(payload, pps) {
		t.Fatal("random-access frame lacks cached VPS/SPS/PPS")
	}
	_, key = p.accessUnit([][]byte{{2, 1, 5}})
	if key {
		t.Fatal("predicted frame marked as random access")
	}
}

func TestHEVCMain10AndInvalidParameters(t *testing.T) {
	sps, _ := hex.DecodeString("4201010220000000b0000000000099")
	codec, err := hevcCodec(sps)
	if err != nil || codec != "hev1.2.4.L153.B0" {
		t.Fatalf("wrong Main10 codec: %q %v", codec, err)
	}
	for _, data := range [][]byte{nil, {1}, {66, 1, 1}} {
		if _, err := hevcCodec(data); err == nil {
			t.Fatal("invalid SPS accepted")
		}
	}
}
