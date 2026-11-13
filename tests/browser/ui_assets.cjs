/* Same bundled, cacheable fonts as production, served without external traffic. */
const fs=require('node:fs'),path=require('node:path');
const fonts=JSON.parse(fs.readFileSync(path.resolve(__dirname,'../../edge-of-infinity/rootfs/usr/share/edge-of-infinity/edge-ui-fonts.json'),'utf8'));
exports.fontResponse=url=>{let name;try{name=new URL(url).pathname.split('/api/ui/fonts/')[1];}catch(_){return null;}return fonts[name]?{contentType:'font/woff2',body:Buffer.from(fonts[name],'base64')}:null;};
