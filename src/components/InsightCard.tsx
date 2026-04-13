import { motion } from 'motion/react';
import { Play, Tag, Clock, Share2 } from 'lucide-react';
import { Insight } from '../types';

interface InsightCardProps {
  insight: Insight;
  onPlay: (insight: Insight) => void;
  key?: string | number;
}

export default function InsightCard({ insight, onPlay }: InsightCardProps) {
  const moodColors = {
    calm: 'from-blue-500/20 to-indigo-500/20',
    energetic: 'from-orange-500/20 to-red-500/20',
    reflective: 'from-purple-500/20 to-pink-500/20',
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      className={`glass rounded-3xl p-6 relative overflow-hidden group cursor-pointer bg-gradient-to-br ${moodColors[insight.mood]}`}
      onClick={() => onPlay(insight)}
    >
      <div className="flex justify-between items-start mb-4">
        <div>
          <h3 className="text-xl font-medium text-white/90 mb-1">{insight.title}</h3>
          <div className="flex items-center gap-3 text-white/50 text-xs font-mono">
            <span className="flex items-center gap-1">
              <Clock size={12} />
              {new Date(insight.timestamp).toLocaleDateString()}
            </span>
            <span>{Math.round(insight.duration)}s</span>
          </div>
        </div>
        <button className="p-3 bg-white/10 rounded-full hover:bg-white/20 transition-colors">
          <Play size={20} fill="currentColor" />
        </button>
      </div>

      <div className="space-y-3">
        {insight.highlights.map((h, i) => (
          <div key={h.id} className="bg-black/20 rounded-xl p-3 border border-white/5">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[10px] font-bold uppercase tracking-wider text-white/40">{h.tag}</span>
              <span className="text-[10px] font-mono text-white/30">{Math.floor(h.startTime)}s</span>
            </div>
            <p className="text-sm text-white/80 line-clamp-2 italic">"{h.text}"</p>
          </div>
        ))}
      </div>

      <div className="mt-4 flex flex-wrap gap-2">
        {insight.tags.map(tag => (
          <span key={tag} className="px-2 py-1 bg-white/5 rounded-md text-[10px] text-white/60 border border-white/10">
            #{tag}
          </span>
        ))}
      </div>

      <div className="absolute top-4 right-16 opacity-0 group-hover:opacity-100 transition-opacity">
        <button className="p-2 text-white/40 hover:text-white/80">
          <Share2 size={16} />
        </button>
      </div>
    </motion.div>
  );
}
