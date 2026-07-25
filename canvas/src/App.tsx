import { useCallback, useRef, useState } from 'react'
import {
  createShapeId,
  Tldraw,
  toRichText,
  type Editor,
  type TLShapeId,
  type TLShapePartial,
} from 'tldraw'
import 'tldraw/tldraw.css'

const API_URL = 'http://127.0.0.1:8080/api/canvas-data'

interface SessionRow {
  session_id: string
  started_at: string
  status: string
}

interface TaskRow {
  id: number
  title: string
  status: string
}

interface LinkRow {
  task_id: number
  session_id: string
  activity: string
  at: string
}

interface CanvasData {
  sessions: SessionRow[]
  tasks: TaskRow[]
  links: LinkRow[]
}

const BOX_W = 380
const BOX_H = 100
const ROW_GAP = 140
const SESSION_X = 0
const TASK_X = 600

function sessionShapeId(sessionId: string): TLShapeId {
  return createShapeId(`session-${sessionId}`)
}

function taskShapeId(taskId: number): TLShapeId {
  return createShapeId(`task-${taskId}`)
}

function renderCanvas(editor: Editor, data: CanvasData): void {
  editor.deleteShapes(Array.from(editor.getCurrentPageShapeIds()))

  const sessionCenterY = new Map<string, number>()
  const taskCenterY = new Map<number, number>()
  const boxes: TLShapePartial[] = []

  data.sessions.forEach((session, index) => {
    const y = index * ROW_GAP
    sessionCenterY.set(session.session_id, y + BOX_H / 2)
    boxes.push({
      id: sessionShapeId(session.session_id),
      type: 'geo',
      x: SESSION_X,
      y,
      props: {
        geo: 'rectangle',
        w: BOX_W,
        h: BOX_H,
        color: session.status === 'active' ? 'green' : 'grey',
        richText: toRichText(
          `session\n${session.session_id}\n${session.status}`,
        ),
      },
    })
  })

  data.tasks.forEach((task, index) => {
    const y = index * ROW_GAP
    taskCenterY.set(task.id, y + BOX_H / 2)
    boxes.push({
      id: taskShapeId(task.id),
      type: 'geo',
      x: TASK_X,
      y,
      props: {
        geo: 'rectangle',
        w: BOX_W,
        h: BOX_H,
        color: task.status === 'done' ? 'blue' : 'orange',
        richText: toRichText(`task #${task.id}\n${task.title}\n${task.status}`),
      },
    })
  })

  editor.createShapes(boxes)

  const arrows: TLShapePartial[] = []
  data.links.forEach((link, index) => {
    const startY = sessionCenterY.get(link.session_id)
    const endY = taskCenterY.get(link.task_id)
    if (startY === undefined || endY === undefined) return
    arrows.push({
      id: createShapeId(`link-${index}-${link.session_id}-${link.task_id}`),
      type: 'arrow',
      x: 0,
      y: 0,
      props: {
        start: { x: SESSION_X + BOX_W, y: startY },
        end: { x: TASK_X, y: endY },
        richText: toRichText(link.activity),
      },
    })
  })

  editor.createShapes(arrows)
  editor.zoomToFit()
}

function App() {
  const editorRef = useRef<Editor | null>(null)
  const [status, setStatus] = useState<string>('loading...')

  const refresh = useCallback(async () => {
    setStatus('loading...')
    try {
      const response = await fetch(API_URL)
      if (!response.ok) {
        throw new Error(`canvas API returned ${response.status}`)
      }
      const data: CanvasData = await response.json()
      if (editorRef.current) {
        renderCanvas(editorRef.current, data)
      }
      setStatus(
        `${data.sessions.length} sessions, ${data.tasks.length} tasks, ${data.links.length} links`,
      )
    } catch (err) {
      setStatus(`failed to load canvas data: ${(err as Error).message}`)
    }
  }, [])

  const handleMount = useCallback(
    (editor: Editor) => {
      editorRef.current = editor
      void refresh()
    },
    [refresh],
  )

  return (
    <div style={{ position: 'fixed', inset: 0 }}>
      <div
        style={{
          position: 'absolute',
          top: 8,
          left: 8,
          zIndex: 1000,
          display: 'flex',
          gap: 8,
          alignItems: 'center',
          background: 'white',
          padding: '6px 10px',
          borderRadius: 6,
          boxShadow: '0 1px 4px rgba(0,0,0,0.2)',
          fontFamily: 'sans-serif',
          fontSize: 13,
        }}
      >
        <button onClick={() => void refresh()}>Refresh</button>
        <span>{status}</span>
      </div>
      <Tldraw onMount={handleMount} />
    </div>
  )
}

export default App
