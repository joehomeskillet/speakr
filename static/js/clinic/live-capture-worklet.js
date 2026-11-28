class LivePcm extends AudioWorkletProcessor {
    frame = new ArrayBuffer(960);
    view = new DataView(this.frame);
    index = 0;
    peak = 0;

    process(inputs) {
        for (const sample of inputs[0]?.[0] || []) {
            const value = Math.max(-1, Math.min(1, sample));
            this.peak = Math.max(this.peak, Math.abs(value));
            this.view.setInt16(this.index, value * (value < 0 ? 32768 : 32767), true);
            this.index += 2;
            if (this.index === this.frame.byteLength) {
                this.port.postMessage({ pcm: this.frame, peak: this.peak }, [this.frame]);
                this.frame = new ArrayBuffer(960);
                this.view = new DataView(this.frame);
                this.index = 0;
                this.peak = 0;
            }
        }
        return true;
    }
}

registerProcessor('clinic-live-pcm', LivePcm);
