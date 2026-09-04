const { ipcRenderer } = require('electron');
const path = require('path');

// ==========================================
// --- 1. 基礎設定與 UI 元素定義 ---
// ==========================================
window.addEventListener('contextmenu', (e) => e.preventDefault());

const app = new PIXI.Application({
  view: document.createElement('canvas'),
  backgroundAlpha: 0,
  autoStart: true,
  width: 400,              // ✅ 補上預設寬度
  height: 600,             // ✅ 補上預設高度 (與熱修改基準對齊)
  resolution: window.devicePixelRatio || 1,
  autoDensity: true
});
document.getElementById('canvas-container').appendChild(app.view);

const modelUrl = './assets/Murasame/Murasame.model3.json';
let model;

const chatInput = document.getElementById('chat-input');
const dialogueText = document.getElementById('dialogue-text');

function showDialogue(text) {
  dialogueText.innerText = text;
  dialogueText.style.display = 'block';
}

// ==========================================
// --- 2. 音頻與真實聲波分析系統 ---
// ==========================================
window.isSpeaking = false;
const audioPlayer = new Audio();
let audioCtx, analyser, dataArray;

function initAudioAnalyzer() {
  if (!audioCtx) {
    audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    dataArray = new Uint8Array(analyser.frequencyBinCount);
    
    const source = audioCtx.createMediaElementSource(audioPlayer);
    source.connect(analyser);
    analyser.connect(audioCtx.destination);
  }
  if (audioCtx.state === 'suspended') {
    audioCtx.resume();
  }
}

// ==========================================
// --- 3. 高級播放佇列系統 (解決連續回傳覆蓋與卡死) ---
// ==========================================
const playQueue = [];
let isPlayingQueue = false;

function handleBackendResponse(data) {
  console.log("🔍 收到大腦片段，加入排隊:", data);
  playQueue.push(data);
  if (!isPlayingQueue) {
    processQueue();
  }
}

function processQueue() {
  if (playQueue.length === 0) {
    isPlayingQueue = false;
    window.isSpeaking = false;
    try { model.expression(5); } catch(e){} 
    
    if (dialogueText.innerText.includes("思考中")) {
      chatInput.style.display = 'none';
      dialogueText.style.display = 'none';
    }
    return;
  }

  isPlayingQueue = true;
  window.isSpeaking = true;
  const currentData = playQueue.shift(); 

  if (typeof currentData === 'string') {
    console.error("❌ 警告：大腦傳來的是純文字而不是 JSON！");
    showDialogue("【叢雨】\n" + currentData);
  } else {
    const text = currentData.reply_zh || currentData.text || currentData.message || "（大腦傳送了無法辨識的文字...）";
    showDialogue("【叢雨】\n" + text);

    try { if (currentData.emotion !== undefined) model.expression(currentData.emotion); } catch (err) {}
    try { if (currentData.playMotion && currentData.motion) model.motion(currentData.motion); } catch (err) {}
  }

  if (currentData.audio_url) {
    initAudioAnalyzer();
    audioPlayer.src = currentData.audio_url; 
    
    audioPlayer.onended = () => { processQueue(); };
    audioPlayer.onerror = (e) => { 
      console.error("❌ 音檔載入失敗:", currentData.audio_url);
      processQueue(); 
    };
    audioPlayer.play().catch(err => {
      console.error("❌ 播放被系統阻擋:", err);
      processQueue();
    });
  } else {
    setTimeout(processQueue, 2500);
  }
}

function sendActionToMiddleware(action) {
  if (window.isSpeaking || dialogueText.innerText.includes("思考中")) return;
  showDialogue("【叢雨】\n思考中...");
  console.log(`[系統提示] 將動作事件傳送給中轉層: ${action}`);
  sendToBrain("action", action);
}

// ==========================================
// --- 4. WebSocket 連線與大腦中轉層對接 ---
// ==========================================
let ws = null;
let isReconnecting = false;

function connectWebSocket() {
  ws = new WebSocket('ws://localhost:8000/ws');

  ws.onopen = () => {
    console.log('[WebSocket] 叢雨大腦神經網路已連線！');
    isReconnecting = false; 
  };

  ws.onmessage = (event) => {
    try {
      const data = JSON.parse(event.data);
      console.log('[WebSocket] 收到大腦指令:', data);
      handleBackendResponse(data); 
    } catch (err) {
      console.error('解析大腦資料失敗:', err);
    }
  };

  ws.onclose = () => {
    console.warn('[WebSocket] 與大腦失去連線，5秒後嘗試重新連線...');
    if (!isReconnecting) {
      isReconnecting = true;
      setTimeout(connectWebSocket, 5000); 
    }
  };

  ws.onerror = (error) => {
    console.error('[WebSocket] 連線發生錯誤');
    ws.close(); 
  };
}

connectWebSocket();

function sendToBrain(type, content) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify({ type: type, content: content }));
  } else {
    showDialogue("【叢雨】\n(唔... 大腦好像正在重啟中，稍微等本座一下啦！)");
  }
}

// ==========================================
// --- 5. 互動、拖曳與摸頭偵測 ---
// ==========================================
function setupInteraction(model) {
  model.interactive = true;
  let rubCount = 0;
  let lastRubTime = Date.now();

  model.on('pointermove', (e) => {
    const { x, y } = e.data.global;
    const hitAreas = model.hitTest(x, y);

    const isTouchingHead = hitAreas.some(area => 
      area.toLowerCase().includes('hair') || area.toLowerCase().includes('head')
    );

    const now = Date.now();
    if (now - lastRubTime > 1000) rubCount = 0; 

    if (isTouchingHead) {
      if (now - lastRubTime > 150) { 
        rubCount++;
        lastRubTime = now;
        console.log(`摸頭進度: ${rubCount}/10`); 

        if (rubCount >= 10) {
          rubCount = 0; 
          sendActionToMiddleware("摸頭"); 
        }
      }
    }
  });

  window.addEventListener('mousedown', (e) => {
    if (e.button === 2) { 
      ipcRenderer.send('start-right-drag');
    } else if (e.button === 0) { 
      if (e.target.id !== 'chat-input') {
        chatInput.style.display = 'block';
        dialogueText.style.display = 'none';
        setTimeout(() => { chatInput.focus(); }, 50);
      }
    }
  });

  window.addEventListener('mouseup', (e) => {
    if (e.button === 2) ipcRenderer.send('stop-right-drag');
  });

  ipcRenderer.on('global-mouse-move', (event, { x, y }) => {
    if (model && model.internalModel && model.internalModel.focusController) {
      if (window.isSpeaking) {
        model.internalModel.focusController.targetX = 0;
        model.internalModel.focusController.targetY = 0;
        model.internalModel.focusController.x = 0;
        model.internalModel.focusController.y = 0;
      } else {
        model.focus(x, y); 
      }
    }
  });
}

// ==========================================
// --- 6. 主程式載入與開機邏輯 ---
// ==========================================
async function init() {
  const { Live2DModel } = PIXI.live2d;
  
  model = await Live2DModel.from(modelUrl); 
  ipcRenderer.invoke('get-config').then(config => {
  const currentScale = (config && config.model_scale) ? config.model_scale : 1.0;
  applyScale(currentScale);
  });
  
  app.stage.addChild(model);
  setupInteraction(model);

  const bootAudio = new Audio("./assets/Murasame/sounds/bandicam 2021-11-23 02-19-30-578.mp4.wav");
  bootAudio.preload = "auto";

  setTimeout(() => {
    try {
      model.expression(5); 
      model.motion("Status_Landing"); 
      bootAudio.currentTime = 0;
      bootAudio.play().catch(err => console.log("開機無音檔或被阻擋"));
    } catch (e) {}
    
    showDialogue("【叢雨】\n——著陸!");
  }, 500);

  // ==========================================
  // --- GPT-SoVITS 專用自然動態對嘴引擎 ---
  // ==========================================
  let smoothedMouth = 0;
  let talkTime = 0; 

  app.ticker.add((delta) => {
    if (model && model.internalModel && model.internalModel.coreModel) {
      if (window.isSpeaking && analyser) {
        analyser.getByteFrequencyData(dataArray);
        
        // 1. 取得整體音量
        let sum = 0; 
        for (let i = 0; i < 60; i++) { sum += dataArray[i]; }
        const avgVolume = sum / 60;
        
        let targetMouth = 0;

        // 2. 音量大於一定數值，代表真的在發聲
        if (avgVolume > 5) { 
          // 💡 降低速度：從原本的 0.8 改成 0.15，讓開合頻率符合人類真實說話節奏
          talkTime += delta * 0.15; 
          
          // 產生自然的節奏波浪 (0 ~ 1)
          const baseWave = (Math.sin(talkTime) + 1) / 2; 
          
          // 將音量轉換為 0 ~ 1 的比例 
          const volumeRatio = Math.min(1, avgVolume / 60); 
          
          // 💡 終極魔法：60% 靠音量決定嘴巴張多大，40% 靠數學波浪模擬嘴唇的碎動
          targetMouth = (volumeRatio * 0.6) + (baseWave * 0.4); 
          
          // 限制範圍，避免嘴巴張得太誇張破圖
          targetMouth = Math.min(0.8, Math.max(0.1, targetMouth));
        } else {
          // 遇到逗號或換氣，立刻閉嘴
          targetMouth = 0;
        }
        
        // 💡 降低緩衝係數 (從 0.4 降到 0.25)，讓嘴巴動作更柔和、不突兀
        smoothedMouth += (targetMouth - smoothedMouth) * 0.25;
        model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', smoothedMouth);
      } else {
        // 沒說話時，平滑地閉上嘴巴
        smoothedMouth += (0 - smoothedMouth) * 0.25;
        model.internalModel.coreModel.setParameterValueById('ParamMouthOpenY', smoothedMouth);
      }
    }
  }, PIXI.UPDATE_PRIORITY.LOW);
}

// ==========================================
// --- 7. 輸入框打字送出邏輯 ---
// ==========================================
chatInput.addEventListener('keypress', async (e) => {
  if (e.key === 'Enter') {
    const userMessage = e.target.value.trim();
    if (!userMessage) {
      chatInput.style.display = 'none';
      if (dialogueText.innerText !== '') dialogueText.style.display = 'block';
      return;
    }

    chatInput.value = ''; 
    chatInput.style.display = 'none'; 
    
    if (window.isSpeaking || dialogueText.innerText.includes("思考中")) return;

    showDialogue("【叢雨】\n思考中...");
    console.log(`[系統提示] 將使用者文字傳送給中轉層: ${userMessage}`);

    sendToBrain("text", userMessage);
  }
});

chatInput.addEventListener('blur', () => {
  if (chatInput.value.trim() === '') {
    chatInput.style.display = 'none';
    if (dialogueText.innerText !== '') dialogueText.style.display = 'block';
  }
});

init();

// ==========================================
// --- 8. 熱修改：動態調整模型與介面縮放 ---
// ==========================================

// 建立一個獨立的縮放函數，讓開機與熱修改都能共用
function applyScale(scale) {
  const baseWidth = 400;
  const baseHeight = 600;
  const newWidth = Math.round(baseWidth * scale);
  const newHeight = Math.round(baseHeight * scale);

  // 1. 同步放大 PIXI 畫布，解決高倍數下模型被切斷的問題
  if (typeof app !== 'undefined' && app.renderer) {
    if (app.renderer.width !== newWidth || app.renderer.height !== newHeight) {
      app.renderer.resize(newWidth, newHeight);
    }
  }

  // 2. 修正模型縮放與定位
  if (typeof model !== 'undefined') {
    const baseModelScale = 0.2; 
    model.scale.set(baseModelScale * scale);

    model.x = newWidth / 2 - model.width / 2;
    
    // 【新增】Y 軸偏移量 (負數代表往上移，正數代表往下移)
    model.y = newHeight - model.height - (10 * scale);
  }

  // 3. 修正對話框定位，讓基準點距離隨視窗等比例拉高
  const chatContainer = document.getElementById('chat-container');
  if (chatContainer) {
    chatContainer.style.transform = `scale(${scale})`;
    chatContainer.style.transformOrigin = 'bottom left'; 
    
    // 讓距離底部的像素也乘上倍數，就能完美咬合在角色的相同部位
    const baseBottom = 380; 
    const baseLeft = 10;
    chatContainer.style.bottom = `${baseBottom * scale}px`;
    chatContainer.style.left = `${baseLeft * scale}px`;
  }
}

// 監聽來自設定視窗的熱修改
ipcRenderer.on('scale-model', (event, scale) => {
  applyScale(scale);
});

// 啟動時主動向主進程索取設定檔，確保一開機的縮放與定位就是正確的
ipcRenderer.invoke('get-config').then(config => {
  if (config && config.model_scale) {
    applyScale(config.model_scale);
  }
});