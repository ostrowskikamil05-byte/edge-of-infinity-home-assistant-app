/* Microphone frames: mono PCM signed 16-bit little endian, 8 kHz, 20 ms. */
class EdgePCM extends AudioWorkletProcessor {
  constructor(){super();this.phase=0;this.sum=0;this.count=0;this.samples=new Int16Array(160);this.position=0;}
  process(inputs){const input=inputs[0]?.[0];if(!input)return true;for(const sample of input){this.sum+=sample;this.count++;this.phase+=8000;if(this.phase>=sampleRate){this.phase-=sampleRate;this.samples[this.position++]=Math.round(Math.max(-1,Math.min(1,this.sum/this.count))*32767);this.sum=0;this.count=0;if(this.position===160){this.port.postMessage(this.samples.buffer,[this.samples.buffer]);this.samples=new Int16Array(160);this.position=0;}}}return true;}
}
registerProcessor('edge-pcm',EdgePCM);
