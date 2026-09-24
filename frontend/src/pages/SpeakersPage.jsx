import { useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api.js'

/**
 * 说话人归一工作台（speaker normalization workbench）。
 *
 * 汇总项目**已保存**的转写片段、校注与规则精确说话人条件中的现有名称；
 * 用户配置多条「旧名 → 目标名」映射，链式映射（A→B→C）在预览中归并到
 * 最终名称。空目标与循环映射由后端拒绝，确认按钮禁用；确认后在后端
 * 一次事务内改写三处说话人字段（保留记录 ID 与其他内容），成功后返回
 * 编辑页，编辑页重新读取项目，引语检索与发言时长随即使用新名称。
 */
export default function SpeakersPage() {
  const { id: projectId } = useParams()
  const navigate = useNavigate()
  const [project, setProject] = useState(null)
  const [rows, setRows] = useState([{ from: '', to: '' }])
  const [preview, setPreview] = useState(null)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [ok, setOk] = useState('')
  const reqSeq = useRef(0)

  useEffect(() => {
    api.getProject(projectId).then(setProject).catch((e) => setError(e.message))
  }, [projectId])

  const mappings = useMemo(
    () => rows.filter((r) => r.from.trim() !== ''),
    [rows],
  )

  // Live preview: every edit re-asks the server, which validates empty
  // targets / cycles and computes final destinations with affected counts.
  useEffect(() => {
    const seq = ++reqSeq.current
    setLoading(true)
    const timer = setTimeout(() => {
      api.previewSpeakerNormalization(
        projectId, mappings.map((r) => ({ from: r.from.trim(), to: r.to.trim() })),
      ).then((data) => {
        if (seq !== reqSeq.current) return
        setPreview(data)
        setError('')
      }).catch((e) => {
        if (seq !== reqSeq.current) return
        setError(e.message)
      }).finally(() => {
        if (seq === reqSeq.current) setLoading(false)
      })
    }, 250)
    return () => clearTimeout(timer)
  }, [projectId, mappings])

  const existingNames = useMemo(
    () => (preview?.names || []).map((n) => n.name),
    [preview],
  )

  const stats = preview?.stats
  const hasChanges = !!stats && stats.changedNameCount > 0
  const invalid = !!error

  const setRow = (i, key, value) => {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [key]: value } : r)))
  }
  const addRow = (preset = {}) =>
    setRows((rs) => [...rs, { from: '', to: '', ...preset }])
  const removeRow = (i) =>
    setRows((rs) => (rs.length === 1
      ? [{ from: '', to: '' }]
      : rs.filter((_, j) => j !== i)))

  const flash = (msg) => {
    setOk(msg)
    setTimeout(() => setOk(''), 4000)
  }

  const confirmApply = async () => {
    if (invalid || !hasChanges) return
    const changed = stats.changedNameCount
    if (!window.confirm(
      `确认把 ${changed} 个旧名归一？\n\n`
      + `受影响：${stats.changedSegments} 个转写片段、`
      + `${stats.changedAnnotations} 条校注、${stats.changedRules} 条规则条件。\n\n`
      + '将在一次事务中更新，记录 ID 与其他内容保持不变。')) {
      return
    }
    setBusy(true); setError('')
    try {
      const res = await api.applySpeakerNormalization(
        projectId,
        mappings.map((r) => ({ from: r.from.trim(), to: r.to.trim() })),
      )
      const c = res.changed
      flash(`归一完成：片段 ${c.segments} · 校注 ${c.annotations} · 规则 ${c.rules}`)
      // return to the editor, which re-reads the project on mount, so quote
      // search and speaking durations use the new names
      setTimeout(() => navigate(`/projects/${projectId}`), 600)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!project && error) return <div className="page error">{error}</div>
  if (!project) return <div className="page">加载中…</div>

  return (
    <div className="page speakers-page">
      <div className="page-head">
        <div className="title-line">
          <Link to={`/projects/${projectId}`} className="back">
            ← 返回校注
          </Link>
          <h2>说话人归一 · {project.name}</h2>
        </div>
        <div className="head-actions">
          <Link className="btn" to={`/projects/${projectId}`}>
            打开时间轴校注
          </Link>
          <button
            className="btn primary"
            disabled={busy || invalid || !hasChanges}
            onClick={confirmApply}
            title={invalid ? '请先解决映射问题'
              : !hasChanges ? '当前映射不会改动任何名称' : ''}
          >
            {busy ? '提交中…' : '✅ 确认归一'}
          </button>
        </div>
      </div>

      <div className="warn-box">
        工作台读取项目<strong>已保存</strong>的数据；编辑页若有未保存修改，请先返回保存。
        归一会同时更新转写片段、校注与规则的<strong>精确说话人条件</strong>三处字段，
        支持链式归并（A→B、B→C 最终都到 C），记录 ID 与其他内容不变。
      </div>
      {error && <div className="error">🚫 {error}，已阻止提交，请修正映射。</div>}
      {ok && <div className="ok">{ok}，正在返回编辑页…</div>}

      <div className="panel">
        <div className="preview-head">
          <h3>映射规则（旧名 → 目标名）</h3>
          <button className="btn small" onClick={() => addRow()}>
            ＋ 添加映射
          </button>
        </div>
        <div className="sp-mapping-list">
          {rows.map((row, i) => (
            <div className="sp-mapping-row" key={i}>
              <input
                list="sp-name-list"
                value={row.from}
                placeholder="现有旧名（精确匹配）"
                onChange={(e) => setRow(i, 'from', e.target.value)}
              />
              <span className="sp-arrow">→</span>
              <input
                list="sp-name-list"
                value={row.to}
                placeholder="目标名（留空将阻止提交）"
                onChange={(e) => setRow(i, 'to', e.target.value)}
              />
              <button
                className="btn icon danger"
                onClick={() => removeRow(i)}
                title="删除该映射"
              >
                ✕
              </button>
            </div>
          ))}
          <datalist id="sp-name-list">
            {existingNames.map((n) => <option key={n} value={n} />)}
          </datalist>
        </div>
        <div className="muted small" style={{ marginTop: 8 }}>
          目标名可以是现有名称（用于归并），也可以是全新名称；自映射
          （A→A）视为不变。
        </div>
      </div>

      {preview && (
        <PreviewPanel
          preview={preview}
          stale={invalid}
          loading={loading}
          onAddMapping={(name) => addRow({ from: name })}
        />
      )}
    </div>
  )
}

function PreviewPanel({ preview, stale, loading, onAddMapping }) {
  const s = preview.stats
  const changedTargets = preview.targets.filter((t) => t.sourceNames.length)
  return (
    <div className="panel sp-preview">
      <div className="preview-head">
        <h3>确认预览{stale && <span className="badge warn"> 映射有问题，以下为上次有效预览</span>}</h3>
        {loading && <span className="muted small">更新中…</span>}
      </div>

      <div className="stats-grid qc-summary">
        <div className="stat-box">
          <strong>现有名称</strong><span>{s.nameCount}</span>
        </div>
        <div className={`stat-box ${s.changedNameCount ? 'has-conflict' : ''}`}>
          <strong>将改名</strong><span>{s.changedNameCount}</span>
        </div>
        <div className="stat-box">
          <strong>受影响片段</strong><span>{s.changedSegments}</span>
        </div>
        <div className="stat-box">
          <strong>受影响校注</strong><span>{s.changedAnnotations}</span>
        </div>
        <div className="stat-box">
          <strong>受影响规则</strong><span>{s.changedRules}</span>
        </div>
      </div>

      {preview.unusedMappings.length > 0 && (
        <div className="warn-box">
          以下 {preview.unusedMappings.length} 条映射的旧名在项目三处数据中
          均不存在，不会产生改动：
          {' '}
          {preview.unusedMappings
            .map((m) => `${m.from} → ${m.to}`).join('；')}
        </div>
      )}

      {changedTargets.length > 0 && (
        <div className="sp-target-groups">
          <h4>归并后的目标名称</h4>
          {changedTargets.map((t) => (
            <div className="sp-target-card" key={t.name}>
              <div className="sp-target-head">
                <strong>{t.name}</strong>
                <span className="muted small">
                  {t.sourceNames.join('、')} → {t.name}
                </span>
              </div>
              <div className="sp-target-counts muted small">
                片段 {t.segments} · 校注 {t.annotations} · 规则 {t.rules}
              </div>
            </div>
          ))}
        </div>
      )}

      <h4 style={{ marginTop: 14 }}>每个名称的最终去向</h4>
      <table className="run-table sp-name-table">
        <thead>
          <tr>
            <th>现有名称</th><th>最终去向</th><th>片段</th>
            <th>校注</th><th>规则</th><th></th>
          </tr>
        </thead>
        <tbody>
          {preview.names.map((n) => (
            <tr key={n.name} className={n.changed ? 'sp-row-changed' : ''}>
              <td>{n.name}</td>
              <td>
                {n.changed
                  ? <span className="sp-dest">{n.chain.join(' → ')}</span>
                  : <span className="muted">保持不变</span>}
              </td>
              <td className="sp-num">{n.segments}</td>
              <td className="sp-num">{n.annotations}</td>
              <td className="sp-num">{n.rules}</td>
              <td>
                {!n.changed && (
                  <button
                    className="btn small"
                    onClick={() => onAddMapping(n.name)}
                  >
                    加为旧名
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
