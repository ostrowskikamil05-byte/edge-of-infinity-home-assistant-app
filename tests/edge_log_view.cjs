const assert=require('node:assert/strict');
require('../edge-of-infinity/rootfs/usr/share/edge-of-infinity/edge-log-view.js');
const view=globalThis.EdgeLogView;
const events=[
 {event:'ui_recording_video_waiting',time:'2026-11-08T12:00:00Z',data:{}},
 {event:'promise_rejection',time:'2026-11-08T12:01:00Z',data:{message:'Subscription not found.'}},
 {event:'rtc_websocket_close',time:'2026-11-08T12:02:00Z',data:{code:1006,expected:true}},
 {event:'ui_recording_video_error',time:'2026-11-08T12:03:00Z',data:{error_code:3}},
 {event:'rtc_websocket_close',time:'2026-11-08T12:04:00Z',data:{code:1006}}
];
const report={device:{capabilities:{audio:'unsupported'},unavailable:['CPU not found']},events};
const rows=view.parse(JSON.stringify(report)+'\n'+JSON.stringify(report));
assert.equal(rows.length,5);assert.equal(rows[0].severity,'warning');assert.equal(rows[1].severity,'error');
assert.equal(rows[2].severity,'ok');assert.equal(rows[3].severity,'warning');assert.equal(rows[4].severity,'ok');
assert.equal(rows[0].category,'live');assert.equal(rows[4].category,'recordings');
assert.equal(view.valueSeverity(report),'ok');assert.equal(view.valueSeverity({camera:{last_error:'RTSP timeout'}}),'error');
assert.equal(view.parse(JSON.stringify({ts:123,ui:{event:'ui_live_frame_load',payload:{index:1}}}))[0].time,123);
const history=view.parse(JSON.stringify({ts:'2026-08-13T11:00:00+0000',key:'front_0',error:'old disk full'}),{origin:'archive',legacyEvent:'recording_cache'});
assert.equal(history[0].event,'recording_cache_error');assert.equal(history[0].origin,'archive');assert.equal(history[0].severity,'error');assert(view.stamp(history[0].time)>0);
assert.equal(view.stamp(1794170904000),1794170904000);assert.equal(view.stamp(1794170904),1794170904000);
assert.equal(view.category('keyframe_onvif_requested'),'live');assert.equal(view.category('ui_recording_canvas_picture'),'recordings');
assert.equal(view.severity({event:'keyframe_onvif_unavailable'}),'warning');
assert.equal(view.severity({event:'recording_autostart_waiting',data:{severity:'info'}}),'ok');
assert.equal(view.severity({event:'recording_autostart_waiting',data:{severity:'warning'}}),'warning');
assert.equal(view.category('rtp_unknown_payload'),'live');
console.log('PASS: structured log groups, deduplication, time order, actual media errors and expected session cancellation');
