import { EventLevel } from '@/types'

// ANSI color codes
export const ANSI = {
  RESET: '\x1b[0m',
  BOLD: '\x1b[1m',
  DIM: '\x1b[2m',

  // Foreground colors
  BLACK: '\x1b[30m',
  RED: '\x1b[31m',
  GREEN: '\x1b[32m',
  YELLOW: '\x1b[33m',
  BLUE: '\x1b[34m',
  MAGENTA: '\x1b[35m',
  CYAN: '\x1b[36m',
  WHITE: '\x1b[37m',
  GRAY: '\x1b[90m',

  // Bright foreground colors
  BRIGHT_RED: '\x1b[91m',
  BRIGHT_GREEN: '\x1b[92m',
  BRIGHT_YELLOW: '\x1b[93m',
  BRIGHT_BLUE: '\x1b[94m',
  BRIGHT_MAGENTA: '\x1b[95m',
  BRIGHT_CYAN: '\x1b[96m',
  BRIGHT_WHITE: '\x1b[97m'
}

// Log level configuration with ANSI colors
export const logLevelColors = {
  [EventLevel.INFO]: {
    color: ANSI.BRIGHT_BLUE,
    emoji: 'ℹ',
    label: 'INFO'
  },
  [EventLevel.WARNING]: {
    color: ANSI.BRIGHT_YELLOW,
    emoji: '⚠',
    label: 'WARN'
  },
  [EventLevel.FAILED]: {
    color: ANSI.BRIGHT_RED,
    emoji: '✗',
    label: 'FAIL'
  },
  [EventLevel.SUCCESS]: {
    color: ANSI.BRIGHT_GREEN,
    emoji: '✓',
    label: 'SUCC'
  },
  [EventLevel.DEBUG]: {
    color: ANSI.BRIGHT_MAGENTA,
    emoji: '⚡',
    label: 'DEBG'
  },
  [EventLevel.PENDING]: {
    color: ANSI.YELLOW,
    emoji: '⏳',
    label: 'PEND'
  },
  [EventLevel.RUNNING]: {
    color: ANSI.CYAN,
    emoji: '↻',
    label: 'RUN '
  },
  [EventLevel.COMPLETED]: {
    color: ANSI.GREEN,
    emoji: '✓',
    label: 'CMPL'
  },
  [EventLevel.GRAPH_APPEND]: {
    color: ANSI.MAGENTA,
    emoji: '📊',
    label: 'GRPH'
  }
}

const defaultColors = {
  color: ANSI.GRAY,
  emoji: '•',
  label: 'INFO'
}

export const formatTime = (date: string): string => {
  return new Date(date).toLocaleTimeString('en-US', {
    hour12: false,
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit'
  })
}

export const formatLogEntry = (timestamp: string, level: EventLevel, message: string): string => {
  const config = logLevelColors[level] || defaultColors
  const time = formatTime(timestamp)

  // Format: [HH:MM:SS] LEVEL emoji message
  return (
    `${ANSI.DIM}[${time}]${ANSI.RESET} ` +
    `${config.color}${ANSI.BOLD}${config.label}${ANSI.RESET} ` +
    `${message}\r\n`
  )
}

export const clearScreen = (): string => {
  return '\x1b[2J\x1b[H'
}

export const formatWelcomeMessage = (): string => {
  return (
    `${ANSI.BRIGHT_RED}${ANSI.BOLD}╭───────────────────────────────────────────╮${ANSI.RESET}\r\n` +
    `${ANSI.BRIGHT_RED}${ANSI.BOLD}│${ANSI.RESET}  ${ANSI.BRIGHT_WHITE}⚡ Fl0sint enrichers terminal${ANSI.RESET}   ${ANSI.BRIGHT_RED}${ANSI.BOLD}│${ANSI.RESET}\r\n` +
    `${ANSI.BRIGHT_RED}${ANSI.BOLD}╰───────────────────────────────────────────╯${ANSI.RESET}\r\n` +
    `${ANSI.DIM}Waiting for investigation activity...${ANSI.RESET}\r\n\r\n`
  )
}
