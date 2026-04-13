/**
 * Utility to process audio blobs: stripping silence and potentially reducing noise.
 */

export async function stripSilence(audioBlob: Blob): Promise<Blob> {
  const audioContext = new AudioContext();
  const arrayBuffer = await audioBlob.arrayBuffer();
  const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);

  // 1. Noise Reduction / Noise Gate
  // We'll apply a simple noise gate by zeroing out samples below a noise floor
  const noiseFloor = 0.01; 
  
  for (let channel = 0; channel < audioBuffer.numberOfChannels; channel++) {
    const data = audioBuffer.getChannelData(channel);
    for (let i = 0; i < data.length; i++) {
      if (Math.abs(data[i]) < noiseFloor) {
        data[i] = 0;
      }
    }
  }

  // 2. Audio Enhancement (Simple Compression/Normalization)
  // This makes the voice more consistent and "present"
  for (let channel = 0; channel < audioBuffer.numberOfChannels; channel++) {
    const data = audioBuffer.getChannelData(channel);
    let max = 0;
    for (let i = 0; i < data.length; i++) {
      if (Math.abs(data[i]) > max) max = Math.abs(data[i]);
    }
    
    if (max > 0) {
      const gain = 0.9 / max; // Normalize to 0.9
      for (let i = 0; i < data.length; i++) {
        data[i] *= gain;
        // Simple soft clipping/compression
        if (data[i] > 0.8) data[i] = 0.8 + (data[i] - 0.8) * 0.2;
        if (data[i] < -0.8) data[i] = -0.8 + (data[i] + 0.8) * 0.2;
      }
    }
  }

  const channelData = audioBuffer.getChannelData(0);
  const sampleRate = audioBuffer.sampleRate;
  
  // 2. Silence Stripping
  const threshold = 0.02; 
  const minSilenceDuration = 0.4; 
  const minSilenceSamples = minSilenceDuration * sampleRate;
  const padding = 0.15; 
  const paddingSamples = padding * sampleRate;

  const speechSegments: { start: number; end: number }[] = [];
  let isSpeech = false;
  let speechStart = 0;
  let silenceCount = 0;

  for (let i = 0; i < channelData.length; i++) {
    const amplitude = Math.abs(channelData[i]);

    if (amplitude > threshold) {
      if (!isSpeech) {
        isSpeech = true;
        speechStart = Math.max(0, i - paddingSamples);
      }
      silenceCount = 0;
    } else {
      if (isSpeech) {
        silenceCount++;
        if (silenceCount > minSilenceSamples) {
          isSpeech = false;
          speechSegments.push({ 
            start: speechStart, 
            end: Math.min(channelData.length, i - minSilenceSamples + paddingSamples) 
          });
        }
      }
    }
  }

  // If still in speech at the end
  if (isSpeech) {
    speechSegments.push({ start: speechStart, end: channelData.length });
  }

  // If no speech detected, return original (maybe it's all very quiet)
  if (speechSegments.length === 0) return audioBlob;

  // Calculate total length of new buffer
  const totalLength = speechSegments.reduce((acc, seg) => acc + (seg.end - seg.start), 0);
  const newBuffer = audioContext.createBuffer(
    audioBuffer.numberOfChannels,
    totalLength,
    sampleRate
  );

  // Copy segments to new buffer
  for (let channel = 0; channel < audioBuffer.numberOfChannels; channel++) {
    const oldData = audioBuffer.getChannelData(channel);
    const newData = newBuffer.getChannelData(channel);
    let offset = 0;
    for (const seg of speechSegments) {
      const segmentData = oldData.subarray(seg.start, seg.end);
      newData.set(segmentData, offset);
      offset += segmentData.length;
    }
  }

  // Convert AudioBuffer back to Blob (WAV format)
  const wavBlob = bufferToWav(newBuffer);
  await audioContext.close();
  return wavBlob;
}

// Simple WAV encoder for AudioBuffer
function bufferToWav(abuffer: AudioBuffer): Blob {
  const numOfChan = abuffer.numberOfChannels;
  const length = abuffer.length * numOfChan * 2 + 44;
  const buffer = new ArrayBuffer(length);
  const view = new DataView(buffer);
  const channels = [];
  let i;
  let sample;
  let offset = 0;
  let pos = 0;

  // write WAVE header
  setUint32(0x46464952);                         // "RIFF"
  setUint32(length - 8);                         // file length - 8
  setUint32(0x45564157);                         // "WAVE"

  setUint32(0x20746d66);                         // "fmt " chunk
  setUint32(16);                                 // length = 16
  setUint16(1);                                  // PCM (uncompressed)
  setUint16(numOfChan);
  setUint32(abuffer.sampleRate);
  setUint32(abuffer.sampleRate * 2 * numOfChan); // avg. bytes/sec
  setUint16(numOfChan * 2);                      // block-align
  setUint16(16);                                 // 16-bit (hardcoded)

  setUint32(0x61746164);                         // "data" - chunk
  setUint32(length - pos - 4);                   // chunk length

  // write interleaved data
  for (i = 0; i < abuffer.numberOfChannels; i++)
    channels.push(abuffer.getChannelData(i));

  while (pos < length) {
    for (i = 0; i < numOfChan; i++) {             // interleave channels
      sample = Math.max(-1, Math.min(1, channels[i][offset])); // clamp
      sample = (0.5 + sample < 0 ? sample * 32768 : sample * 32767) | 0; // scale to 16-bit signed int
      view.setInt16(pos, sample, true);          // write 16-bit sample
      pos += 2;
    }
    offset++;                                     // next sample
  }

  return new Blob([buffer], { type: "audio/wav" });

  function setUint16(data: number) {
    view.setUint16(pos, data, true);
    pos += 2;
  }

  function setUint32(data: number) {
    view.setUint32(pos, data, true);
    pos += 4;
  }
}
