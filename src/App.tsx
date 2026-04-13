import { useState, useRef, useEffect } from 'react';
import { motion, AnimatePresence } from 'motion/react';
import { Mic, Square, List, Plus, X, ChevronLeft, Play, Pause, Share2, Search, Send, MessageSquare, Sparkles } from 'lucide-react';
import Waveform from './components/Waveform';
import InsightCard from './components/InsightCard';
import { Insight } from './types';
import { stripSilence } from './lib/audioUtils';

export default function App() {
  const [isRecording, setIsRecording] = useState(false);
  const [recordTime, setRecordTime] = useState(0);
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [insights, setInsights] = useState<Insight[]>([]);
  const [isProcessing, setIsProcessing] = useState(false);
  const [view, setView] = useState<'gallery' | 'record' | 'detail' | 'rag'>('gallery');
  const [selectedInsight, setSelectedInsight] = useState<Insight | null>(null);
  const [isPlaying, setIsPlaying] = useState(false);
  
  // RAG State
  const [ragQuery, setRagQuery] = useState('');
  const [ragResponse, setRagResponse] = useState<string | null>(null);
  const [isRagLoading, setIsRagLoading] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<number>(0);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const snippetTimeoutRef = useRef<number | null>(null);

  const playSegment = (startTime: number, endTime: number) => {
    if (audioRef.current) {
      if (snippetTimeoutRef.current) window.clearTimeout(snippetTimeoutRef.current);
      audioRef.current.currentTime = startTime;
      audioRef.current.play();
      setIsPlaying(true);
      const duration = (endTime - startTime) * 1000;
      snippetTimeoutRef.current = window.setTimeout(() => {
        if (audioRef.current) {
          audioRef.current.pause();
          setIsPlaying(false);
        }
      }, duration);
    }
  };

  const togglePlay = () => {
    if (audioRef.current) {
      if (isPlaying) {
        audioRef.current.pause();
        if (snippetTimeoutRef.current) window.clearTimeout(snippetTimeoutRef.current);
      } else {
        audioRef.current.play();
      }
      setIsPlaying(!isPlaying);
    }
  };

  useEffect(() => {
    if (view !== 'detail' && audioRef.current) {
      audioRef.current.pause();
      setIsPlaying(false);
      if (snippetTimeoutRef.current) window.clearTimeout(snippetTimeoutRef.current);
    }
  }, [view]);

  useEffect(() => {
    const saved = localStorage.getItem('insight_recorder_data');
    if (saved) {
      try {
        const parsed = JSON.parse(saved);
        setInsights(parsed);
      } catch (e) {
        console.error('Failed to parse saved insights', e);
      }
    }
  }, []);

  useEffect(() => {
    localStorage.setItem('insight_recorder_data', JSON.stringify(insights));
  }, [insights]);

  const fileInputRef = useRef<HTMLInputElement>(null);

  const processAudio = async (audioBlob: Blob, duration: number, originalMimeType: string) => {
    setIsProcessing(true);
    try {
      const cleanAudioBlob = await stripSilence(audioBlob);
      const audioUrl = URL.createObjectURL(cleanAudioBlob);
      
      const reader = new FileReader();
      reader.readAsDataURL(cleanAudioBlob);
      reader.onloadend = async () => {
        const base64data = (reader.result as string).split(',')[1];
        
        // Call backend for analysis and vector storage
        const response = await fetch('/api/analyze', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ audioBase64: base64data, mimeType: 'audio/wav' })
        });
        
        if (!response.ok) throw new Error('Analysis failed');
        const analysis = await response.json();
        
        const newInsight: Insight = {
          timestamp: Date.now(),
          duration: duration,
          audioUrl,
          ...analysis,
          highlights: analysis.highlights.map((h: any) => ({
            ...h,
            id: Math.random().toString(36).substr(2, 9),
          }))
        };

        setInsights(prev => [newInsight, ...prev]);
        setIsProcessing(false);
        setView('gallery');
      };
    } catch (error) {
      console.error('Processing failed', error);
      setIsProcessing(false);
      alert('Failed to process audio. Please try again.');
    }
  };

  const handleFileUpload = async (e: any) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const audioContext = new AudioContext();
    try {
      const arrayBuffer = await file.arrayBuffer();
      const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
      const duration = audioBuffer.duration;
      await processAudio(file, duration, file.type);
    } catch (err) {
      console.error('File processing failed', err);
      alert('Unsupported or corrupted audio file.');
    } finally {
      audioContext.close();
    }
  };

  const startRecording = async () => {
    try {
      const audioStream = await navigator.mediaDevices.getUserMedia({ audio: true });
      setStream(audioStream);
      const mediaRecorder = new MediaRecorder(audioStream);
      mediaRecorderRef.current = mediaRecorder;
      chunksRef.current = [];
      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) chunksRef.current.push(e.data);
      };
      mediaRecorder.onstop = async () => {
        const rawAudioBlob = new Blob(chunksRef.current, { type: 'audio/webm' });
        await processAudio(rawAudioBlob, recordTime, 'audio/webm');
      };
      mediaRecorder.start();
      setIsRecording(true);
      setView('record');
      setRecordTime(0);
      timerRef.current = window.setInterval(() => {
        setRecordTime(prev => prev + 1);
      }, 1000);
    } catch (err) {
      console.error('Failed to start recording', err);
      alert('Microphone access denied.');
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && isRecording) {
      mediaRecorderRef.current.stop();
      setIsRecording(false);
      clearInterval(timerRef.current);
      if (stream) {
        stream.getTracks().forEach(track => track.stop());
        setStream(null);
      }
    }
  };

  const handleRagQuery = async () => {
    if (!ragQuery.trim()) return;
    setIsRagLoading(true);
    try {
      const response = await fetch('/api/query', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: ragQuery })
      });
      
      if (!response.ok) throw new Error('Query failed');
      const data = await response.json();
      setRagResponse(data.answer);
    } catch (error) {
      console.error('RAG Query Error:', error);
      setRagResponse("I'm sorry, I couldn't process that request right now.");
    } finally {
      setIsRagLoading(false);
    }
  };

  const filteredInsights = insights.filter(i => 
    i.title.toLowerCase().includes(searchQuery.toLowerCase()) ||
    i.transcript.toLowerCase().includes(searchQuery.toLowerCase()) ||
    i.tags.some(t => t.toLowerCase().includes(searchQuery.toLowerCase()))
  );

  const moodGradients = {
    calm: 'from-blue-900/20 via-black to-indigo-900/20',
    energetic: 'from-orange-900/20 via-black to-red-900/20',
    reflective: 'from-purple-900/20 via-black to-pink-900/20',
    default: 'from-zinc-900/20 via-black to-zinc-900/20'
  };

  const currentMood = view === 'detail' && selectedInsight ? selectedInsight.mood : 'default';

  return (
    <div className={`min-h-screen max-w-md mx-auto relative flex flex-col bg-[#050505] transition-colors duration-1000 bg-gradient-to-br ${moodGradients[currentMood as keyof typeof moodGradients]}`}>
      {/* Header */}
      <header className="p-6 flex justify-between items-center z-10">
        {view === 'gallery' ? (
          <>
            <h1 className="text-2xl font-display italic font-black tracking-tight">Insights</h1>
            <div className="flex items-center gap-2">
              <button 
                onClick={() => setView('rag')}
                className="p-2 text-white/40 hover:text-white transition-colors"
              >
                <MessageSquare size={24} />
              </button>
              <button className="p-2 text-white/40 hover:text-white transition-colors">
                <List size={24} />
              </button>
            </div>
          </>
        ) : (
          <>
            <button 
              onClick={() => setView('gallery')}
              className="p-2 -ml-2 text-white/60 hover:text-white transition-colors flex items-center gap-1"
            >
              <ChevronLeft size={24} />
              <span className="text-sm font-medium">Back</span>
            </button>
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
              <span className="text-xs font-mono text-white/40 uppercase tracking-widest">Live</span>
            </div>
          </>
        )}
      </header>

      {/* Main Content */}
      <main className="flex-1 px-6 pb-32 overflow-y-auto scrollbar-hide">
        <AnimatePresence mode="wait">
          {view === 'gallery' && (
            <motion.div 
              key="gallery"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="space-y-6"
            >
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 text-white/20" size={18} />
                <input 
                  type="text"
                  placeholder="Search insights..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="w-full bg-white/5 border border-white/10 rounded-2xl py-3 pl-10 pr-4 text-sm focus:outline-none focus:border-white/20 transition-colors"
                />
              </div>

              {filteredInsights.length === 0 ? (
                <div className="h-[50vh] flex flex-col items-center justify-center text-center space-y-4 opacity-30">
                  <Mic size={48} strokeWidth={1} />
                  <p className="text-sm font-light">No insights found.<br/>Try recording or uploading a new one.</p>
                </div>
              ) : (
                filteredInsights.map(insight => (
                  <InsightCard 
                    key={insight.id} 
                    insight={insight} 
                    onPlay={(i) => {
                      setSelectedInsight(i);
                      setView('detail');
                    }}
                  />
                ))
              )}
            </motion.div>
          )}

          {view === 'rag' && (
            <motion.div 
              key="rag"
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: 20 }}
              className="h-full flex flex-col space-y-6"
            >
              <div className="space-y-2">
                <h2 className="text-2xl font-display italic font-black">Ask your Insights</h2>
                <p className="text-xs text-white/40">Query across all your meetings and voice notes.</p>
              </div>

              <div className="flex-1 space-y-4 overflow-y-auto">
                <div className="glass rounded-2xl p-4 min-h-[100px] relative">
                  {isRagLoading ? (
                    <div className="flex items-center gap-2 text-white/40 italic text-sm">
                      <Sparkles size={16} className="animate-pulse" />
                      Thinking...
                    </div>
                  ) : ragResponse ? (
                    <div className="text-sm leading-relaxed text-white/80 whitespace-pre-wrap">
                      {ragResponse}
                    </div>
                  ) : (
                    <div className="text-sm text-white/20 italic">
                      "What was the project name mentioned in the last meeting?"
                    </div>
                  )}
                </div>
              </div>

              <div className="flex gap-2">
                <input 
                  type="text"
                  value={ragQuery}
                  onChange={(e) => setRagQuery(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleRagQuery()}
                  placeholder="Type your question..."
                  className="flex-1 bg-white/5 border border-white/10 rounded-2xl py-3 px-4 text-sm focus:outline-none focus:border-white/20"
                />
                <button 
                  onClick={handleRagQuery}
                  disabled={isRagLoading}
                  className="w-12 h-12 rounded-2xl bg-white text-black flex items-center justify-center disabled:opacity-50"
                >
                  <Send size={20} />
                </button>
              </div>
            </motion.div>
          )}

          {view === 'record' && (
            <motion.div 
              key="record"
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 1.05 }}
              className="h-full flex flex-col items-center justify-center space-y-12"
            >
              <div className="text-center space-y-2">
                <h2 className="text-4xl font-mono font-medium tracking-tighter">
                  {Math.floor(recordTime / 60)}:{(recordTime % 60).toString().padStart(2, '0')}
                </h2>
                <p className="text-xs text-white/40 uppercase tracking-[0.2em]">Recording Thoughts</p>
              </div>

              <div className="w-full max-w-xs aspect-square relative flex items-center justify-center">
                <div className="absolute inset-0 rounded-full border border-white/5 scale-110" />
                <div className="absolute inset-0 rounded-full border border-white/10 scale-125" />
                <motion.button
                  whileTap={{ scale: 0.9 }}
                  onClick={stopRecording}
                  className="w-48 h-48 rounded-full bg-white flex items-center justify-center text-black shadow-[0_0_50px_rgba(255,255,255,0.2)] z-10"
                >
                  <Square size={48} fill="currentColor" />
                </motion.button>
                <div className="absolute -bottom-12 left-0 right-0">
                  <Waveform stream={stream} isRecording={isRecording} color="white" />
                </div>
              </div>
              <p className="text-sm text-white/60 font-light italic text-center px-8">
                "Speak freely. I'm listening for the impactful parts."
              </p>
            </motion.div>
          )}

          {view === 'detail' && selectedInsight && (
            <motion.div 
              key="detail"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0, x: -20 }}
              className="space-y-8"
            >
              <div className="space-y-4">
                <h2 className="text-3xl font-display italic font-black leading-tight">
                  {selectedInsight.title}
                </h2>
                <div className="flex items-center gap-4 bg-white/5 p-4 rounded-2xl border border-white/10">
                  <button 
                    onClick={togglePlay}
                    className="w-12 h-12 rounded-full bg-white text-black flex items-center justify-center flex-shrink-0"
                  >
                    {isPlaying ? <Pause size={24} fill="currentColor" /> : <Play size={24} fill="currentColor" className="ml-1" />}
                  </button>
                  <div className="flex-1">
                    <div className="text-[10px] font-mono text-white/40 uppercase mb-1">Full Recording</div>
                    <audio 
                      ref={audioRef}
                      src={selectedInsight.audioUrl} 
                      onPlay={() => setIsPlaying(true)}
                      onPause={() => setIsPlaying(false)}
                      onEnded={() => setIsPlaying(false)}
                      className="hidden" 
                    />
                    <div className="h-1 bg-white/10 rounded-full overflow-hidden">
                      <motion.div 
                        className="h-full bg-white"
                        animate={{ width: isPlaying ? '100%' : '0%' }}
                        transition={{ duration: selectedInsight.duration, ease: "linear" }}
                      />
                    </div>
                  </div>
                </div>
              </div>

              <div className="space-y-2">
                <h3 className="text-xs font-bold uppercase tracking-widest text-white/30">Summary</h3>
                <p className="text-sm text-white/80 leading-relaxed italic">
                  {selectedInsight.summary}
                </p>
              </div>

              <div className="space-y-6">
                <h3 className="text-xs font-bold uppercase tracking-widest text-white/30">Gold Nuggets</h3>
                <div className="space-y-4">
                  {selectedInsight.highlights.map(h => (
                    <motion.div 
                      key={h.id} 
                      whileHover={{ scale: 1.02 }}
                      className="glass rounded-2xl p-5 space-y-3 relative group"
                    >
                      <div className="flex justify-between items-center">
                        <span className="px-2 py-0.5 bg-white/10 rounded text-[10px] font-bold text-white/60 uppercase">{h.tag}</span>
                        <div className="flex items-center gap-2">
                          <button className="text-white/40 hover:text-white"><Share2 size={14}/></button>
                        </div>
                      </div>
                      <p className="text-lg font-medium leading-snug">"{h.text}"</p>
                      <button 
                        onClick={() => playSegment(h.startTime, h.endTime)}
                        className="flex items-center gap-2 text-[10px] font-mono text-white/30 hover:text-white transition-colors"
                      >
                        <div className="w-6 h-6 rounded-full bg-white/10 flex items-center justify-center group-hover:bg-white group-hover:text-black transition-colors">
                          <Play size={10} fill="currentColor" />
                        </div>
                        <span>Play Snippet ({Math.floor(h.startTime)}s - {Math.floor(h.endTime)}s)</span>
                      </button>
                    </motion.div>
                  ))}
                </div>
              </div>

              <div className="space-y-4">
                <h3 className="text-xs font-bold uppercase tracking-widest text-white/30">Full Transcript</h3>
                <p className="text-white/60 leading-relaxed text-sm font-light">
                  {selectedInsight.transcript}
                </p>
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </main>

      {/* Bottom Controls */}
      {view === 'gallery' && (
        <div className="fixed bottom-0 left-0 right-0 p-8 bg-gradient-to-t from-black to-transparent pointer-events-none">
          <div className="max-w-md mx-auto flex justify-center items-center gap-6 pointer-events-auto">
            <input 
              type="file" 
              ref={fileInputRef} 
              onChange={handleFileUpload} 
              accept="audio/*,video/*" 
              className="hidden" 
            />
            <motion.button
              whileHover={{ scale: 1.1 }}
              whileTap={{ scale: 0.9 }}
              onClick={() => fileInputRef.current?.click()}
              className="w-12 h-12 rounded-full bg-white/10 text-white flex items-center justify-center border border-white/10"
            >
              <Plus size={24} />
            </motion.button>

            <motion.button
              whileHover={{ scale: 1.05 }}
              whileTap={{ scale: 0.95 }}
              onClick={startRecording}
              className="w-20 h-20 rounded-full bg-white text-black flex items-center justify-center shadow-2xl"
            >
              <Mic size={32} />
            </motion.button>

            <motion.button
              whileHover={{ scale: 1.1 }}
              whileTap={{ scale: 0.9 }}
              className="w-12 h-12 rounded-full bg-white/10 text-white flex items-center justify-center border border-white/10 opacity-50"
            >
              <Share2 size={20} />
            </motion.button>
          </div>
        </div>
      )}

      {/* Processing Overlay */}
      <AnimatePresence>
        {isProcessing && (
          <motion.div 
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-50 bg-black/90 backdrop-blur-md flex flex-col items-center justify-center p-12 text-center space-y-8"
          >
            <div className="relative w-24 h-24">
              <motion.div 
                animate={{ rotate: 360 }}
                transition={{ duration: 4, repeat: Infinity, ease: "linear" }}
                className="absolute inset-0 rounded-full border-2 border-dashed border-white/20"
              />
              <motion.div 
                animate={{ scale: [1, 1.2, 1] }}
                transition={{ duration: 2, repeat: Infinity }}
                className="absolute inset-4 rounded-full bg-white/10 flex items-center justify-center"
              >
                <div className="w-4 h-4 rounded-full bg-white" />
              </motion.div>
            </div>
            <div className="space-y-2">
              <h3 className="text-xl font-medium">Distilling Insights</h3>
              <p className="text-sm text-white/40 font-light">
                Our AI is listening for the gold nuggets in your thoughts...
              </p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
