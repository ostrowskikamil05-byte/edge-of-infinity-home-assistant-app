/* Microphone frames: mono PCM signed 16-bit little endian, 8 kHz, 20 ms. */
class EdgePCM extends AudioWorkletProcessor {
  constructor(){super();this.phase=0;this.sum=0;this.count=0;this.samples=new Int16Array(160);this.position=0;}
  process(inputs){const input=inputs[0]?.[0];if(!input)return true;for(const sample of input){this.sum+=sample;this.count++;this.phase+=8000;if(this.phase>=sampleRate){this.phase-=sampleRate;this.samples[this.position++]=Math.round(Math.max(-1,Math.min(1,this.sum/this.count))*32767);this.sum=0;this.count=0;if(this.position===160){this.port.postMessage(this.samples.buffer,[this.samples.buffer]);this.samples=new Int16Array(160);this.position=0;}}}return true;}
}
registerProcessor('edge-pcm',EdgePCM);

/* A continuous camera PCM stream. Native per-packet BufferSource resampling
 * restarts its filter on every 20 ms G711 block and can create boundary peaks.
 * Keep source samples in one bounded ring, resampling across packet boundaries.
 */
class EdgePlayback extends AudioWorkletProcessor {
  constructor(options={}){
    super();this.archive=options.processorOptions?.mode==='archive';this.bufferSeconds=this.archive ? .6 : .24;this.startSeconds=this.archive ? .12 : .08;
    this.size=this.archive?65536:32768;this.mask=this.size-1;this.ring=[new Float32Array(this.size),new Float32Array(this.size)];this.outputFrames=0;this.underruns=0;this.overruns=0;this.droppedFrames=0;this.statsFrames=0;this.reset();
    this.port.onmessage=event=>{if(event.data.type==='clear')this.reset();else if(event.data.type==='pcm')this.append(event.data);};
  }
  reset(){this.read=0;this.write=0;this.rate=0;this.playing=false;this.waited=0;this.attack=0;this.nextStamp=null;this.last=[0,0];this.tail=0;}
  append(packet){
    const {channels,rate,timestamp}=packet,n=channels?.[0]?.length;
    if(!Number.isFinite(rate)||rate<8000||rate>96000||!n||n>rate*.25||channels.length>2||channels.some(c=>c.length!==n))return;
    if(this.rate&&this.rate!==rate)this.reset();this.rate=rate;
    const gap=this.nextStamp==null||!Number.isFinite(timestamp)?0:(timestamp-this.nextStamp)/1e6;
    if(gap<-.002&&gap> -2){this.droppedFrames+=n;return;}
    if(gap>.24||gap<= -2){this.read=this.write;this.playing=false;this.waited=0;this.attack=0;this.overruns++;}
    const silence=gap>.002&&gap<=.24?Math.round(gap*rate):0;
    const limit=Math.min(this.size-4,Math.floor(rate*this.bufferSeconds)),count=n+silence;
    if(this.write-this.read+count>limit){const next=this.write+count-limit;this.droppedFrames+=Math.ceil(next-this.read);this.read=next;this.overruns++;this.attack=0;}
    for(let i=0;i<silence;i++){const p=this.write++&this.mask;this.ring[0][p]=this.ring[1][p]=0;}
    for(let i=0;i<n;i++){const p=this.write++&this.mask;this.ring[0][p]=channels[0][i];this.ring[1][p]=(channels[1]||channels[0])[i];}
    this.nextStamp=Number.isFinite(timestamp)?timestamp+n/rate*1e6:null;
  }
  process(inputs,outputs){
    const out=outputs[0],length=out[0].length;
    if(!this.playing&&this.rate&&this.write-this.read>2){
      this.waited+=length;
      // Wall-clock prebuffering also protects AAC delivered in 128 ms bursts.
      if(this.waited>=sampleRate*this.startSeconds&&this.write-this.read>=this.rate*.04){this.playing=true;this.attack=0;this.port.postMessage({type:'started'});}
    }
    for(let i=0;i<length;i++){
      if(this.playing&&this.write-Math.floor(this.read)<3){this.playing=false;this.waited=0;this.underruns++;this.tail=Math.ceil(sampleRate*.003);}
      if(this.playing){
        const p=Math.floor(this.read),t=this.read-p,blend=Math.min(1,this.attack++/(sampleRate*.005));
        for(let ch=0;ch<out.length;ch++){
          const r=this.ring[Math.min(ch,1)],b=r[p&this.mask],a=p?r[(p-1)&this.mask]:b,c=r[(p+1)&this.mask],d=r[(p+2)&this.mask];
          const value=b+.5*t*(c-a+t*(2*a-5*b+4*c-d+t*(3*(b-c)+d-a)));
          out[ch][i]=Math.max(-1,Math.min(1,value))*blend;this.last[ch]=out[ch][i];
        }
        this.read+=this.rate/sampleRate;this.outputFrames++;
      }else if(this.tail>0){for(let ch=0;ch<out.length;ch++)out[ch][i]=this.last[ch]*this.tail/Math.ceil(sampleRate*.003);this.tail--;}
    }
    this.statsFrames+=length;
    if(this.statsFrames>=sampleRate){this.statsFrames=0;this.port.postMessage({type:'stats',mode:this.archive?'archive':'live',buffer_limit_ms:this.bufferSeconds*1000,queued_ms:this.rate?Math.round(Math.max(0,this.write-this.read)/this.rate*1000):0,underruns:this.underruns,overruns:this.overruns,dropped_frames:this.droppedFrames,output_frames:this.outputFrames});}
    return true;
  }
}
registerProcessor('edge-playback',EdgePlayback);
