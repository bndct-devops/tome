// The reader's goals for the Overview (/api/goals), kept for the session, and
// how many there were last time in this browser (what the skeleton reserves).
import { useEffect, useState } from 'react'
import { listGoals, type Goal } from '@/lib/goals'

const COUNT_KEY = 'tome_stats_goal_count'
const MAX_REMEMBERED = 24

export function rememberedGoalCount(): number {
  try {
    const n = Number(localStorage.getItem(COUNT_KEY) ?? '0')
    return Number.isInteger(n) && n >= 0 && n <= MAX_REMEMBERED ? n : 0
  } catch {
    return 0
  }
}
function rememberCount(n: number) {
  try {
    localStorage.setItem(COUNT_KEY, String(Math.min(MAX_REMEMBERED, n)))
  } catch {
    // storage unavailable - the next skeleton reserves nothing
  }
}

// The answer is kept for the session, so coming back to the Overview is instant.
let cached: Goal[] | null = null

/** The reader's goals (null until the first answer), and a reload after an edit. */
export function useGoals() {
  const [goals, setGoals] = useState<Goal[] | null>(cached)
  const [rev, setRev] = useState(0)
  useEffect(() => {
    let current = true
    listGoals()
      .then((g) => {
        cached = g
        rememberCount(g.length)
        if (current) setGoals(g)
      })
      .catch(() => { if (current) setGoals((g) => g ?? []) })
    return () => { current = false }
  }, [rev])
  return { goals, reload: () => setRev((r) => r + 1) }
}

