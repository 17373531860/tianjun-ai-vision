// framePump 背压协调器单测（vitest 跑道首个用例）。
// 语义: 每工位同时只解一帧; 解码在途时新帧覆盖式积压, 只追最新。
import { describe, it, expect, vi } from 'vitest'
import { createFramePump } from '../framePump'

function deferred() {
  let resolve, reject
  const promise = new Promise((res, rej) => { resolve = res; reject = rej })
  return { promise, resolve, reject }
}

const tick = () => new Promise(r => setTimeout(r, 0))

describe('createFramePump', () => {
  it('空闲工位的帧立即解码', () => {
    const decode = vi.fn(() => Promise.resolve())
    const pump = createFramePump(decode)
    pump.push(0, 'f1')
    expect(decode).toHaveBeenCalledOnce()
    expect(decode).toHaveBeenCalledWith(0, 'f1')
  })

  it('在途时新帧覆盖积压, 完成后只追最新一帧', async () => {
    const gates = []
    const decode = vi.fn(() => {
      const d = deferred()
      gates.push(d)
      return d.promise
    })
    const pump = createFramePump(decode)

    pump.push(0, 'f1')
    pump.push(0, 'f2')  // 积压
    pump.push(0, 'f3')  // 覆盖 f2
    expect(decode).toHaveBeenCalledTimes(1)
    expect(pump._state().pending[0]).toBe('f3')

    gates[0].resolve()
    await tick()
    // f2 被丢弃, 直接解 f3
    expect(decode).toHaveBeenCalledTimes(2)
    expect(decode).toHaveBeenLastCalledWith(0, 'f3')
    expect(pump._state().pending[0]).toBeUndefined()

    gates[1].resolve()
    await tick()
    expect(pump._state().inFlight[0]).toBe(false)
  })

  it('工位间互不阻塞', async () => {
    const gates = {}
    const decode = vi.fn(ch => {
      const d = deferred()
      gates[ch] = d
      return d.promise
    })
    const pump = createFramePump(decode)
    pump.push(0, 'a')
    pump.push(1, 'b')
    expect(decode).toHaveBeenCalledTimes(2)
    gates[0].resolve(); gates[1].resolve()
    await tick()
    expect(pump._state().inFlight).toEqual({ 0: false, 1: false })
  })

  it('解码抛错不卡死泵, 后续帧照常', async () => {
    let shouldThrow = true
    const decode = vi.fn(() => {
      if (shouldThrow) throw new Error('boom')
      return Promise.resolve()
    })
    const pump = createFramePump(decode)
    pump.push(0, 'bad')
    await tick()
    shouldThrow = false
    pump.push(0, 'good')
    await tick()
    expect(decode).toHaveBeenCalledTimes(2)
    expect(pump._state().inFlight[0]).toBe(false)
  })

  it('reset 清空在途与积压', async () => {
    const d = deferred()
    const decode = vi.fn(() => d.promise)
    const pump = createFramePump(decode)
    pump.push(0, 'f1')
    pump.push(0, 'f2')
    pump.reset()
    expect(pump._state()).toEqual({ inFlight: {}, pending: {} })
    d.resolve()
    await tick()
    // reset 后完成回调不再追积压帧
    expect(decode).toHaveBeenCalledTimes(1)
  })
})
