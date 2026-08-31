const { spawn } = require('child_process');
const path = require('path');
const http = require('http');

const BACKEND_DIR = path.join(__dirname, 'pet_backend');
const FRONTEND_DIR = __dirname; 

let backendProcess = null;
let frontendProcess = null;
let isBackendRestarting = false;

// --- [新增] 安全重啟限制參數 ---
let restartCount = 0;
const MAX_RESTARTS = 3;
const STABLE_RUNTIME = 15000;
let stableTimer = null;
// -------------------------------

function checkBackendReady(callback) {
  const req = http.get('http://localhost:8000/docs', (res) => {
    if (res.statusCode < 500) {
      callback();
    } else {
      setTimeout(() => checkBackendReady(callback), 1000);
    }
  });

  req.on('error', () => {
    setTimeout(() => checkBackendReady(callback), 1000);
  });
}

function startBackend() {
  console.log('----------------------------------------');
  console.log('[Launcher] 正在啟動 Python 中轉層...');
  
  backendProcess = spawn('python', ['main.py'], {
    cwd: BACKEND_DIR,
    shell: true,
    stdio: 'inherit'
  });

  // 穩定計時器：如果後端活超過 15 秒，重置重啟次數
  if (stableTimer) clearTimeout(stableTimer);
  stableTimer = setTimeout(() => {
    if (restartCount > 0) {
      console.log('[Launcher] 🌟 後端已穩定運行，重啟計數器歸零。');
      restartCount = 0; 
    }
  }, STABLE_RUNTIME);

  backendProcess.on('exit', (code) => {
    console.log(`\n[Launcher] 警告：Python 中轉層已關閉 (Code: ${code})`);
    
    // 熔斷機制：超過次數就不再重啟
    restartCount++;
    if (restartCount > MAX_RESTARTS) {
      console.log(`\n[Launcher] ❌ 嚴重錯誤：連續崩潰超過 ${MAX_RESTARTS} 次！觸發安全熔斷。`);
      stopFrontend(); 
      return; 
    }

    isBackendRestarting = true;
    console.log(`[Launcher] 準備進行第 ${restartCount}/${MAX_RESTARTS} 次自動重啟...`);

    stopFrontend(() => {
      console.log('[Launcher] 正在等待後端完全關閉並準備重啟...');
      setTimeout(() => {
        startBackend();
        
        console.log('[Launcher] 正在等待後端初始化完成...');
        checkBackendReady(() => {
          console.log('[Launcher] ✅ 後端已完全就緒！準備啟動前端...');
          setTimeout(() => {
            isBackendRestarting = false;
            startFrontend();
          }, 1000); 
        });
      }, 2000);
    });
  });
}

function startFrontend() {
  if (isBackendRestarting) return;
  console.log('[Launcher] 正在啟動 Electron 前端...');
  
  // 🚀 關鍵修改：避免 npm start 的熱重載監聽，改為直接啟動 electron
  frontendProcess = spawn('npx', ['electron', '.'], {
    cwd: FRONTEND_DIR,
    shell: true,
    stdio: 'inherit'
  });

  frontendProcess.on('exit', (code) => {
    if (!isBackendRestarting) {
      console.log(`[Launcher] 前端已手動關閉 (Code: ${code})，正在安全結束後端與整個系統...`);
      // 先用 taskkill 把 Python 後端與它的子進程全部砍掉
      if (backendProcess) {
        spawn('taskkill', ['/pid', backendProcess.pid, '/f', '/t'], { shell: true });
      }
      // 等待 1 秒確保清理完畢後，結束 launch.js 自身
      setTimeout(() => {
        process.exit(0);
      }, 1000);
    }
  });
}

function stopFrontend(callback) {
  if (frontendProcess) {
    console.log('[Launcher] 正在關閉 Electron 前端...');
    spawn('taskkill', ['/pid', frontendProcess.pid, '/f', '/t'], { shell: true })
      .on('exit', () => {
        frontendProcess = null;
        if (callback) callback();
      });
  } else {
    if (callback) callback();
  }
}

// === 初始啟動流程 ===
startBackend();

console.log('[Launcher] 正在等待後端初始化完成（首度開機）...');
checkBackendReady(() => {
  console.log('[Launcher] ✅ 後端已完全就緒！正在啟動 Electron 前端...');
  startFrontend();
});