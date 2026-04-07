function installHybridAudioProbe(page, options = {}) {
  const probeName = options.probeName || "__econAudioProbe";
  return page.evaluate(
    ({ probeName: injectedProbeName }) => {
      if (window[injectedProbeName]?.record && window[injectedProbeName]?.snapshot) {
        return { installed: true, alreadyPresent: true };
      }

      const trackedAudios = [];
      const trackedDataChannels = [];
      const peerConnections = [];
      const events = [];
      const transcriptEvents = [];
      const speechEvents = [];
      const audioEvents = [];
      const speechFetches = [];
      const seenAudios = new WeakSet();
      const seenDataChannels = new WeakSet();

      const now = () => Date.now();
      const safeText = (value) => String(value ?? "").replace(/\s+/g, " ").trim();

      const pushEvent = (type, detail) => {
        events.push({ type, at: now(), ...detail });
      };

      const classifyMessage = (payload) => {
        if (!payload || typeof payload !== "object") {
          return null;
        }
        const eventType =
          safeText(payload.type) ||
          safeText(payload.event_type) ||
          safeText(payload.event?.type) ||
          safeText(payload.data?.type);
        if (!eventType) {
          return null;
        }
        const serialized = JSON.stringify(payload);
        return { eventType, serialized };
      };

      const registerAudio = (audio) => {
        if (!(audio instanceof HTMLAudioElement) || seenAudios.has(audio)) {
          return audio;
        }
        seenAudios.add(audio);
        trackedAudios.push(audio);
        audio.dataset.econProbeAudio = `audio-${trackedAudios.length}`;
        const captureState = (kind) => {
          const tracks =
            audio.srcObject instanceof MediaStream
              ? audio
                  .srcObject
                  .getAudioTracks()
                  .map((track) => ({
                    id: track.id,
                    enabled: track.enabled,
                    muted: track.muted,
                    readyState: track.readyState,
                    label: track.label,
                  }))
              : [];
          audioEvents.push({
            kind,
            id: audio.dataset.econProbeAudio || "",
            paused: audio.paused,
            muted: audio.muted,
            readyState: audio.readyState,
            currentTime: audio.currentTime,
            src: audio.getAttribute("src") || "",
            currentSrc: audio.currentSrc || "",
            hasSrcObject: audio.srcObject instanceof MediaStream,
            srcObjectTrackCount: tracks.length,
            srcObjectTracks: tracks,
            at: now(),
          });
        };
        audio.addEventListener("play", () => {
          captureState("play");
          pushEvent("audio.play", { id: audio.dataset.econProbeAudio || "" });
        });
        audio.addEventListener("pause", () => {
          captureState("pause");
          pushEvent("audio.pause", { id: audio.dataset.econProbeAudio || "" });
        });
        audio.addEventListener("ended", () => {
          captureState("ended");
          pushEvent("audio.ended", { id: audio.dataset.econProbeAudio || "" });
        });
        audio.addEventListener("error", () => {
          captureState("error");
          pushEvent("audio.error", { id: audio.dataset.econProbeAudio || "" });
        });
        return audio;
      };

      const wrapDataChannel = (channel, label) => {
        if (!channel || seenDataChannels.has(channel)) {
          return channel;
        }
        seenDataChannels.add(channel);
        trackedDataChannels.push({
          label: safeText(label),
          readyState: channel.readyState,
          bufferedAmount: channel.bufferedAmount,
          at: now(),
        });

        const originalAddEventListener = channel.addEventListener.bind(channel);
        channel.addEventListener = (type, listener, options) => {
          if (type !== "message" || typeof listener !== "function") {
            return originalAddEventListener(type, listener, options);
          }
          return originalAddEventListener(
            type,
            (event) => {
              captureDataChannelMessage(label, event);
              return listener.call(channel, event);
            },
            options,
          );
        };

        const proto = Object.getPrototypeOf(channel);
        const descriptor = proto ? Object.getOwnPropertyDescriptor(proto, "onmessage") : null;
        if (descriptor?.set && descriptor?.get) {
          Object.defineProperty(channel, "onmessage", {
            configurable: true,
            enumerable: true,
            get() {
              return descriptor.get.call(channel);
            },
            set(handler) {
              if (typeof handler !== "function") {
                descriptor.set.call(channel, handler);
                return;
              }
              descriptor.set.call(channel, (event) => {
                captureDataChannelMessage(label, event);
                return handler.call(channel, event);
              });
            },
          });
        }

        channel.addEventListener("open", () => {
          pushEvent("channel.open", { label: safeText(label), readyState: channel.readyState });
        });
        channel.addEventListener("close", () => {
          pushEvent("channel.close", { label: safeText(label), readyState: channel.readyState });
        });
        channel.addEventListener("error", () => {
          pushEvent("channel.error", { label: safeText(label), readyState: channel.readyState });
        });
        return channel;
      };

      const captureDataChannelMessage = (label, event) => {
        const raw = event?.data;
        const text = typeof raw === "string" ? raw : "";
        let payload = null;
        if (text) {
          try {
            payload = JSON.parse(text);
          } catch {
            payload = null;
          }
        }
        const classified = classifyMessage(payload);
        const entry = {
          label: safeText(label),
          rawType: typeof raw,
          rawText: text || null,
          parsedType: classified?.eventType || null,
          at: now(),
        };
        if (classified) {
          entry.parsed = payload;
          if (classified.eventType === "input_audio_buffer.speech_started") {
            speechEvents.push({ kind: "started", ...entry });
          } else if (classified.eventType === "input_audio_buffer.speech_stopped") {
            speechEvents.push({ kind: "stopped", ...entry });
          } else if (
            classified.eventType === "response.output_audio_transcript.delta" ||
            classified.eventType === "response.audio_transcript.delta" ||
            classified.eventType === "response.output_audio_transcript.done" ||
            classified.eventType === "response.audio_transcript.done" ||
            classified.eventType === "response.output_text.delta" ||
            classified.eventType === "response.output_text.done"
          ) {
            transcriptEvents.push(entry);
          }
          if (classified.eventType === "response.done") {
            pushEvent("response.done", entry);
          }
        }
        pushEvent("channel.message", entry);
      };

      const originalCreateElement = Document.prototype.createElement;
      Document.prototype.createElement = function patchedCreateElement(tagName, options) {
        const element = originalCreateElement.call(this, tagName, options);
        if (String(tagName).toLowerCase() === "audio") {
          registerAudio(element);
        }
        return element;
      };

      const originalPlay = HTMLMediaElement.prototype.play;
      HTMLMediaElement.prototype.play = function patchedPlay(...args) {
        if (this instanceof HTMLAudioElement) {
          pushEvent("media.play", {
            id: this.dataset.econProbeAudio || "",
            src: this.getAttribute("src") || "",
            currentSrc: this.currentSrc || "",
          });
        }
        return originalPlay.apply(this, args);
      };

      const originalPause = HTMLMediaElement.prototype.pause;
      HTMLMediaElement.prototype.pause = function patchedPause(...args) {
        if (this instanceof HTMLAudioElement) {
          pushEvent("media.pause", {
            id: this.dataset.econProbeAudio || "",
            src: this.getAttribute("src") || "",
            currentSrc: this.currentSrc || "",
          });
        }
        return originalPause.apply(this, args);
      };

      const originalCreateDataChannel = RTCPeerConnection.prototype.createDataChannel;
      RTCPeerConnection.prototype.createDataChannel = function patchedCreateDataChannel(label, init) {
        const channel = originalCreateDataChannel.call(this, label, init);
        wrapDataChannel(channel, label);
        return channel;
      };

      const originalFetch = window.fetch.bind(window);
      window.fetch = async (input, init) => {
        const response = await originalFetch(input, init);
        try {
          const url =
            typeof input === "string"
              ? input
              : input instanceof Request
                ? input.url
                : String(input ?? "");
          if (url.includes("/api/audio/speech")) {
            const requestBody =
              typeof init?.body === "string"
                ? init.body
                : input instanceof Request && typeof input.body === "string"
                  ? input.body
                  : "";
            const cloned = response.clone();
            const bytes = Array.from(new Uint8Array(await cloned.arrayBuffer()));
            speechFetches.push({
              at: now(),
              url,
              requestBody,
              mimeType: cloned.headers.get("content-type") || "",
              bytes,
            });
            pushEvent("speech.fetch", {
              url,
              byteLength: bytes.length,
            });
          }
        } catch (error) {
          pushEvent("speech.fetch_failed", {
            message: error instanceof Error ? error.message : String(error),
          });
        }
        return response;
      };

      window.__econAudioProbe = {
        trackedAudios,
        trackedDataChannels,
        peerConnections,
        events,
        transcriptEvents,
        speechEvents,
        audioEvents,
        speechFetches,
        snapshot() {
          return trackedAudios.map((audio) => {
            const tracks =
              audio.srcObject instanceof MediaStream
                ? audio.srcObject.getAudioTracks().map((track) => ({
                    id: track.id,
                    enabled: track.enabled,
                    muted: track.muted,
                    readyState: track.readyState,
                    label: track.label,
                  }))
                : [];
            return {
              id: audio.dataset.econProbeAudio || "",
              paused: audio.paused,
              muted: audio.muted,
              readyState: audio.readyState,
              currentTime: audio.currentTime,
              currentSrc: audio.currentSrc || "",
              src: audio.getAttribute("src") || "",
              hasSrcObject: audio.srcObject instanceof MediaStream,
              srcObjectTrackCount: tracks.length,
              srcObjectTracks: tracks,
              canCapture: typeof audio.captureStream === "function",
            };
          });
        },
        getSignals() {
          return {
            trackedAudios: trackedAudios.length,
            trackedDataChannels: trackedDataChannels.length,
            peerConnections: peerConnections.length,
            speechFetches: speechFetches.length,
            speechStarted: speechEvents.filter((entry) => entry.kind === "started").length,
            speechStopped: speechEvents.filter((entry) => entry.kind === "stopped").length,
            transcriptEvents: transcriptEvents.length,
            audioEvents: audioEvents.length,
            lastSpeechEvent: speechEvents.at(-1) || null,
            lastTranscriptEvent: transcriptEvents.at(-1) || null,
            lastAudioEvent: audioEvents.at(-1) || null,
            lastSpeechFetch: speechFetches.at(-1)
              ? {
                  at: speechFetches.at(-1).at,
                  url: speechFetches.at(-1).url,
                  mimeType: speechFetches.at(-1).mimeType,
                  byteLength: speechFetches.at(-1).bytes.length,
                }
              : null,
          };
        },
        takeSpeechFetches() {
          const items = speechFetches.slice();
          speechFetches.length = 0;
          return items;
        },
        async record(durationMs = 15000) {
          const AudioContextCtor = window.AudioContext || window.webkitAudioContext;
          if (!AudioContextCtor) {
            return { error: "AudioContext unavailable", tracked: trackedAudios.length, signals: this.getSignals() };
          }
          const audioContext = new AudioContextCtor();
          if (audioContext.state !== "running") {
            try {
              await audioContext.resume();
            } catch {}
          }

          const destination = audioContext.createMediaStreamDestination();
          const seenStreams = new WeakSet();
          const liveSources = [];

          const attachFromAudios = () => {
            let attachedNow = 0;
            for (const audio of trackedAudios) {
              const attachStream = (stream, origin) => {
                if (!(stream instanceof MediaStream) || seenStreams.has(stream)) {
                  return false;
                }
                const tracks = stream.getAudioTracks();
                if (tracks.length === 0) {
                  return false;
                }
                seenStreams.add(stream);
                try {
                  const source = audioContext.createMediaStreamSource(stream);
                  source.connect(destination);
                  liveSources.push(source);
                  attachedNow += 1;
                  pushEvent("probe.stream.attached", {
                    origin,
                    trackCount: tracks.length,
                  });
                  return true;
                } catch (error) {
                  pushEvent("probe.stream.attach_failed", {
                    origin,
                    message: error instanceof Error ? error.message : String(error),
                  });
                  return false;
                }
              };

              if (audio.srcObject instanceof MediaStream) {
                attachStream(audio.srcObject, `${audio.dataset.econProbeAudio || "audio"}.srcObject`);
              }
              if (typeof audio.captureStream === "function") {
                try {
                  const captured = audio.captureStream();
                  attachStream(captured, `${audio.dataset.econProbeAudio || "audio"}.captureStream`);
                } catch (error) {
                  pushEvent("probe.capture_failed", {
                    id: audio.dataset.econProbeAudio || "",
                    message: error instanceof Error ? error.message : String(error),
                  });
                }
              }
            }
            return attachedNow;
          };

          const sourceDeadline = Date.now() + Math.min(Math.max(durationMs, 2000), 10000);
          while (liveSources.length === 0 && Date.now() < sourceDeadline) {
            attachFromAudios();
            if (liveSources.length === 0) {
              await new Promise((resolve) => window.setTimeout(resolve, 220));
            }
          }

          if (liveSources.length === 0 || destination.stream.getAudioTracks().length === 0) {
            await audioContext.close().catch(() => undefined);
            return {
              error: "No assistant audio stream was captureable",
              tracked: trackedAudios.length,
              signals: this.getSignals(),
              snapshot: this.snapshot(),
            };
          }

          const mimeType = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
            ? "audio/webm;codecs=opus"
            : "audio/webm";
          const chunks = [];
          const recorder = new MediaRecorder(destination.stream, { mimeType });
          recorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) {
              chunks.push(event.data);
            }
          };

          const stopPromise = new Promise((resolve) => {
            recorder.addEventListener("stop", resolve, { once: true });
          });

          recorder.start(250);
          await new Promise((resolve) => window.setTimeout(resolve, durationMs));
          recorder.stop();
          await stopPromise;

          const blob = new Blob(chunks, { type: mimeType });
          const bytes = Array.from(new Uint8Array(await blob.arrayBuffer()));
          destination.stream.getTracks().forEach((track) => track.stop());
          await audioContext.close().catch(() => undefined);
          return {
            bytes,
            mimeType,
            tracked: trackedAudios.length,
            sourceCount: liveSources.length,
            signals: this.getSignals(),
            events,
            speechEvents,
            transcriptEvents,
            audioEvents,
            speechFetches: speechFetches.map((entry) => ({
              at: entry.at,
              url: entry.url,
              requestBody: entry.requestBody,
              mimeType: entry.mimeType,
              byteLength: entry.bytes.length,
            })),
          };
        },
      };

      Array.from(document.querySelectorAll("audio")).forEach((audio) => registerAudio(audio));
      return { installed: true, alreadyPresent: false, trackedAudios: trackedAudios.length };
    },
    { probeName },
  );
}

async function captureHybridAudio(page, durationMs, options = {}) {
  const probeName = options.probeName || "__econAudioProbe";
  return page.evaluate(
    async ({ durationMs: recordDurationMs, probeName: injectedProbeName }) => {
      const probe = window[injectedProbeName];
      if (!probe || typeof probe.record !== "function") {
        return { error: "Audio probe unavailable" };
      }
      return probe.record(recordDurationMs);
    },
    { durationMs, probeName },
  );
}

module.exports = {
  installHybridAudioProbe,
  captureHybridAudio,
};
