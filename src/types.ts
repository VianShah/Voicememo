export interface Insight {
  id: string;
  timestamp: number;
  duration: number;
  title: string;
  transcript: string;
  summary: string;
  highlights: Highlight[];
  mood: 'calm' | 'energetic' | 'reflective';
  audioUrl: string;
  tags: string[];
}

export interface Highlight {
  id: string;
  startTime: number;
  endTime: number;
  text: string;
  tag: '#Realization' | '#ActionItem' | '#Memory';
}
