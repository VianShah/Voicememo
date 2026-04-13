// Structured logger for the Express service
// Outputs JSON-like lines with timestamps for easy grep/parsing

type LogLevel = 'info' | 'warn' | 'error' | 'debug';

function formatLog(level: LogLevel, message: string, meta?: Record<string, any>) {
  const entry = {
    ts: new Date().toISOString(),
    level: level.toUpperCase(),
    msg: message,
    ...(meta ?? {}),
  };
  const line = JSON.stringify(entry);

  if (level === 'error') {
    console.error(line);
  } else if (level === 'warn') {
    console.warn(line);
  } else {
    console.log(line);
  }
}

export const logger = {
  info:  (msg: string, meta?: Record<string, any>) => formatLog('info', msg, meta),
  warn:  (msg: string, meta?: Record<string, any>) => formatLog('warn', msg, meta),
  error: (msg: string, meta?: Record<string, any>) => formatLog('error', msg, meta),
  debug: (msg: string, meta?: Record<string, any>) => formatLog('debug', msg, meta),
};
