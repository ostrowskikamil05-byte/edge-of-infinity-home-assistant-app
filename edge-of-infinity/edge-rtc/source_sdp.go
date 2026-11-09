package main

import (
	"encoding/json"
	"log"
	"strconv"
	"strings"

	"github.com/bluenviron/gortsplib/v5/pkg/base"
	"github.com/bluenviron/gortsplib/v5/pkg/description"
	"github.com/bluenviron/gortsplib/v5/pkg/format"
	"github.com/bluenviron/gortsplib/v5/pkg/sdp"
)

// Some cameras advertise an optional AAC talk format without its decoder
// configuration. gortsplib rejects the entire description, including valid
// forward video/audio. Remove only invalid AAC alternatives from explicitly
// sendonly audio (the ONVIF backchannel); never invent AAC parameters or change
// the camera's forward media. Valid G711 talk alternatives remain available.
func filterInvalidBackchannelAAC(body []byte) ([]byte, []map[string]any) {
	if len(body) > 65536 {
		return body, nil
	}
	var session sdp.SessionDescription
	if session.Unmarshal(body) != nil {
		return body, nil
	}
	var changes []map[string]any
	medias := session.MediaDescriptions[:0]
	for index, media := range session.MediaDescriptions {
		back, conflicting := false, false
		for _, attribute := range media.Attributes {
			switch attribute.Key {
			case "sendonly":
				back = true
			case "recvonly", "sendrecv", "inactive":
				conflicting = true
			}
		}
		if media.MediaName.Media != "audio" || !back || conflicting {
			medias = append(medias, media)
			continue
		}
		dropped := map[string]bool{}
		kept := media.MediaName.Formats[:0]
		for _, pt := range media.MediaName.Formats {
			aac := false
			for _, attribute := range media.Attributes {
				if attribute.Key != "rtpmap" {
					continue
				}
				fields := strings.Fields(attribute.Value)
				if len(fields) == 2 && fields[0] == pt {
					codec := strings.ToLower(strings.SplitN(fields[1], "/", 2)[0])
					aac = codec == "mpeg4-generic" || codec == "mp4a-latm"
					break
				}
			}
			if _, err := format.Unmarshal(media, pt); aac && err != nil {
				number, err := strconv.Atoi(pt)
				if err != nil || number < 0 || number > 127 {
					return body, nil
				}
				dropped[pt] = true
				changes = append(changes, map[string]any{"media_index": index + 1, "payload_type": number, "code": "invalid_backchannel_aac"})
			} else {
				kept = append(kept, pt)
			}
		}
		media.MediaName.Formats = kept
		attributes := media.Attributes[:0]
		for _, attribute := range media.Attributes {
			fields := strings.Fields(attribute.Value)
			if (attribute.Key == "rtpmap" || attribute.Key == "fmtp" || attribute.Key == "rtcp-fb") && len(fields) > 0 && dropped[fields[0]] {
				continue
			}
			attributes = append(attributes, attribute)
		}
		media.Attributes = attributes
		if len(kept) > 0 {
			medias = append(medias, media)
		}
	}
	if len(changes) == 0 {
		return body, nil
	}
	session.MediaDescriptions = medias
	// Keep unrelated forward/structural errors visible. Validate the complete
	// candidate before replacing the library response used for SETUP/PLAY.
	var checked description.Session
	if checked.Unmarshal(&session) != nil {
		return body, nil
	}
	repaired, err := session.Marshal()
	if err != nil {
		return body, nil
	}
	return repaired, changes
}

func (s *source) handleRTSPResponse(response *base.Response) {
	s.connection.response(response) // retain the camera's original declarations
	if !s.connection.isSDPResponse(response) {
		return
	}
	body, changes := filterInvalidBackchannelAAC(response.Body)
	if len(changes) == 0 {
		return
	}
	response.Body = body
	s.connection.filteredOptionalAudio(changes)
	entry, _ := json.Marshal(map[string]any{"event": "rtsp_optional_audio_filtered", "camera_id": s.cfg.ID, "formats": changes,
		"forward_media_changed": false, "same_connection": true})
	log.Printf("rtsp_source %s", entry)
}
