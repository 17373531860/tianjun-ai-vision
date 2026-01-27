<template>
  <div class="grid grid-cols-12 gap-6 h-full">
    <!-- Camera Feed Section (Main) -->
    <div class="col-span-8 bg-industrial-panel border border-gray-700 rounded-lg p-1 flex flex-col shadow-lg relative overflow-hidden group">
        <div class="absolute top-0 left-0 px-2 py-1 bg-black/60 text-xs text-tianjun-blue font-mono uppercase z-10 border-b border-r border-tianjun-blue/30 rounded-br">
            Live Stream: CAM-01
        </div>
        
        <!-- Placeholder for Video Stream -->
        <div class="flex-1 bg-black flex items-center justify-center relative">
            <div class="text-gray-600 flex flex-col items-center">
                <svg xmlns="http://www.w3.org/2000/svg" class="h-16 w-16 mb-4 animate-pulse" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                    <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z" />
                </svg>
                <span>Waiting for Camera Input...</span>
            </div>
            
            <!-- Scan Overlay Effect -->
            <div class="absolute inset-0 bg-gradient-to-b from-transparent via-tianjun-blue/10 to-transparent h-8 w-full animate-scan pointer-events-none"></div>
        </div>

        <!-- Controls -->
        <div class="h-12 bg-slate-900 border-t border-gray-700 flex items-center justify-between px-4">
             <div class="flex gap-2">
                 <button class="bg-green-600 hover:bg-green-700 text-white px-3 py-1 rounded text-sm font-bold shadow transition">START</button>
                 <button class="bg-red-600 hover:bg-red-700 text-white px-3 py-1 rounded text-sm font-bold shadow transition">STOP</button>
             </div>
             <div class="text-xs text-gray-400 font-mono">
                 FPS: 0 | Latency: 0ms
             </div>
        </div>
    </div>

    <!-- Stats Panel (Right Side) -->
    <div class="col-span-4 flex flex-col gap-6">
        <!-- Top Stats -->
        <div class="grid grid-cols-2 gap-4">
             <div class="bg-industrial-panel border border-gray-700 p-4 rounded-lg shadow-lg relative overflow-hidden">
                 <div class="text-gray-400 text-xs uppercase tracking-widest mb-1">Total Count</div>
                 <div class="text-3xl font-bold text-white font-mono">1,024</div>
                 <div class="absolute right-0 top-0 w-2 h-full bg-blue-500 opacity-20"></div>
             </div>
             <div class="bg-industrial-panel border border-gray-700 p-4 rounded-lg shadow-lg relative overflow-hidden">
                 <div class="text-gray-400 text-xs uppercase tracking-widest mb-1">NG Count</div>
                 <div class="text-3xl font-bold text-red-500 font-mono">12</div>
                 <div class="absolute right-0 top-0 w-2 h-full bg-red-500 opacity-20"></div>
             </div>
        </div>

        <!-- Chart Placeholder -->
        <div class="flex-1 bg-industrial-panel border border-gray-700 rounded-lg p-4 shadow-lg flex flex-col">
            <h3 class="text-sm font-bold text-gray-300 mb-4 border-l-4 border-tianjun-blue pl-2 uppercase">Detection Stats</h3>
            <div class="flex-1 flex items-center justify-center border border-dashed border-gray-700 rounded bg-black/20">
                <span class="text-gray-500 text-sm">Chart Visualization Area</span>
            </div>
        </div>

        <!-- Logs -->
        <div class="h-48 bg-industrial-panel border border-gray-700 rounded-lg p-4 shadow-lg overflow-hidden flex flex-col">
             <h3 class="text-sm font-bold text-gray-300 mb-2 border-l-4 border-yellow-500 pl-2 uppercase">System Logs</h3>
             <div class="flex-1 overflow-y-auto text-xs font-mono text-gray-400 space-y-1 custom-scrollbar">
                 <p>[10:00:01] System started.</p>
                 <p>[10:00:02] Model 'bestnew2.pt' loaded.</p>
                 <p>[10:00:05] Use Camera: VID_1BCF&PID_0B16.</p>
                 <p class="text-green-500">[10:00:10] Detection running...</p>
             </div>
        </div>
    </div>
  </div>
</template>

<style scoped>
@keyframes scan {
  0% { top: -10%; }
  100% { top: 110%; }
}
.animate-scan {
  animation: scan 3s linear infinite;
}
</style>
