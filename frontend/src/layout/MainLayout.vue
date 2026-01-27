<template>
  <div class="min-h-screen bg-industrial-dark text-white font-sans flex flex-col">
    <!-- Header -->
    <header class="h-16 bg-industrial-panel border-b border-gray-700 flex items-center px-6 justify-between shadow-lg z-10">
      <div class="flex items-center gap-4">
        <h1 class="text-2xl font-bold tracking-wider text-tianjun-blue drop-shadow-md">
          TIANJUN <span class="text-white text-lg font-normal">Machine Vision</span>
        </h1>
      </div>
      <div class="flex items-center gap-4">
        <span class="text-gray-400 text-sm">2026-01-24 10:00:00</span>
        <div class="px-3 py-1 bg-blue-900 rounded text-xs border border-blue-700">Guest</div>
      </div>
    </header>

    <div class="flex flex-1 overflow-hidden">
      <!-- Sidebar -->
      <aside class="w-20 bg-industrial-panel border-r border-gray-700 flex flex-col items-center py-4 gap-6 z-10">
        <router-link to="/" class="p-2 rounded hover:bg-slate-700 transition" title="Dashboard">
          <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6 text-tianjun-blue" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2V6zM14 6a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2V6zM4 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2H6a2 2 0 01-2-2v-2zM14 16a2 2 0 012-2h2a2 2 0 012 2v2a2 2 0 01-2 2h-2a2 2 0 01-2-2v-2z" />
          </svg>
        </router-link>
        <div class="p-2 rounded hover:bg-slate-700 transition cursor-pointer" title="Projects">
             <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 11H5m14 0a2 2 0 012 2v6a2 2 0 01-2 2H5a2 2 0 01-2-2v-6a2 2 0 012-2m14 0V9a2 2 0 00-2-2M5 11V9a2 2 0 012-2m0 0V5a2 2 0 012-2h6a2 2 0 012 2v2M7 7h10" />
            </svg>
        </div>
         <div class="p-2 rounded hover:bg-slate-700 transition cursor-pointer" title="Settings">
             <svg xmlns="http://www.w3.org/2000/svg" class="h-6 w-6 text-gray-400" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
        </div>
      </aside>

      <!-- Main Content -->
      <main class="flex-1 bg-black/20 p-6 relative overflow-auto">
        <!-- Background Grid Effect -->
        <div class="absolute inset-0 z-[-1] opacity-10" style="background-image: radial-gradient(#4b5563 1px, transparent 1px); background-size: 20px 20px;"></div>
        
        <router-view></router-view>
      </main>
    </div>
  </div>
</template>

<script setup>
</script>
