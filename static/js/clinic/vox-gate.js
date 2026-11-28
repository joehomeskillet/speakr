// Match the detection defaults in src/clinic/vox_detector.py.
export const VOX_CONFIG = Object.freeze({
    thresholdDbfs: -40,
    hysteresisDb: 6,
    preRollMs: 500,
    postRollMs: 1500,
    frameMs: 20,
    minSpeechMs: 100,
});

const MIN_DBFS = -96;

export function rmsDbfs(samples) {
    if (samples.length === 0) return MIN_DBFS;
    let sum = 0;
    for (const sample of samples) sum += sample * sample;
    const rms = Math.sqrt(sum / samples.length);
    return rms <= 0 ? MIN_DBFS : Math.max(MIN_DBFS, 20 * Math.log10(rms));
}

export function planVoxSegments(levels, cfg = VOX_CONFIG) {
    const frameMs = cfg.frameMs;
    const minSpeechFrames = Math.max(1, Math.floor(cfg.minSpeechMs / frameMs));
    const postRollFrames = Math.max(0, Math.floor(cfg.postRollMs / frameMs));
    const continueThreshold = cfg.thresholdDbfs - cfg.hysteresisDb;
    const segments = [];
    let recording = false;
    let aboveCount = 0;
    let runStart = -1;
    let belowCount = 0;
    let lastAbove = -1;
    let start = null;

    for (let i = 0; i < levels.length; i += 1) {
        const level = levels[i];
        if (!recording) {
            if (level >= cfg.thresholdDbfs) {
                if (aboveCount === 0) runStart = i;
                aboveCount += 1;
                if (aboveCount >= minSpeechFrames) {
                    start = Math.max(0, runStart * frameMs - cfg.preRollMs);
                    recording = true;
                    belowCount = 0;
                    lastAbove = i;
                }
            } else {
                aboveCount = 0;
                runStart = -1;
            }
            continue;
        }
        if (level >= continueThreshold) {
            belowCount = 0;
            lastAbove = i;
        } else {
            belowCount += 1;
            if (belowCount >= Math.max(1, postRollFrames)) {
                segments.push([start, (lastAbove + 1) * frameMs + cfg.postRollMs]);
                recording = false;
                aboveCount = 0;
                runStart = -1;
                start = null;
            }
        }
    }
    if (recording && start !== null) segments.push([start, levels.length * frameMs]);

    const merged = [];
    for (const segment of segments) {
        const previous = merged[merged.length - 1];
        if (previous && segment[0] - previous[1] < cfg.postRollMs) previous[1] = segment[1];
        else merged.push([segment[0], segment[1]]);
    }
    return merged;
}

export function encodeWav(channels, sampleRate, ranges = [[0, channels[0].length]]) {
    const count = channels.length;
    const length = ranges.reduce((total, [start, end]) => total + end - start, 0);
    const bytes = 44 + length * count * 2;
    const buffer = new ArrayBuffer(bytes);
    const view = new DataView(buffer);
    const text = (offset, value) => {
        for (let i = 0; i < value.length; i += 1) view.setUint8(offset + i, value.charCodeAt(i));
    };
    text(0, 'RIFF');
    view.setUint32(4, bytes - 8, true);
    text(8, 'WAVE');
    text(12, 'fmt ');
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true);
    view.setUint16(22, count, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, sampleRate * count * 2, true);
    view.setUint16(32, count * 2, true);
    view.setUint16(34, 16, true);
    text(36, 'data');
    view.setUint32(40, length * count * 2, true);
    let offset = 44;
    for (const [start, end] of ranges) {
        for (let i = start; i < end; i += 1) {
            for (const channel of channels) {
                const sample = Math.max(-1, Math.min(1, channel[i]));
                view.setInt16(offset, sample < 0 ? sample * 32768 : sample * 32767, true);
                offset += 2;
            }
        }
    }
    return buffer;
}

export async function gateAudioBlob(blob, cfg = VOX_CONFIG) {
    const AudioContext = globalThis.AudioContext || globalThis.webkitAudioContext;
    if (!blob.size || !AudioContext) return blob;
    let context;
    try {
        context = new AudioContext();
        const decoded = await context.decodeAudioData(await blob.arrayBuffer());
        const { length, sampleRate } = decoded;
        const channels = Array.from({ length: decoded.numberOfChannels }, (_, index) => decoded.getChannelData(index));
        const frameSize = Math.max(1, Math.round(sampleRate * cfg.frameMs / 1000));
        const levels = [];
        for (let offset = 0; offset < length; offset += frameSize) {
            const end = Math.min(length, offset + frameSize);
            let level = MIN_DBFS;
            for (const channel of channels) {
                level = Math.max(level, rmsDbfs(channel.subarray(offset, end)));
            }
            levels.push(level);
        }
        const sampleIndex = (ms) => Math.min(length, Math.round(ms * frameSize / cfg.frameMs));
        const ranges = planVoxSegments(levels, cfg).map(([start, end]) => [sampleIndex(start), sampleIndex(end)]);
        if (!ranges.length || (ranges.length === 1 && ranges[0][0] === 0 && ranges[0][1] === length)) return blob;
        return new Blob([encodeWav(channels, sampleRate, ranges)], { type: 'audio/wav' });
    } catch {
        // Preserve playable capture on unsupported formats or decode failure.
        return blob;
    } finally {
        await context?.close().catch(() => {});
    }
}
