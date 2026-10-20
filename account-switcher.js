'use strict';

const accountAPI=window.REVIEW_API_BASE||`${location.protocol==='https:'?'https:':'http:'}//${location.hostname||'127.0.0.1'}:8000`;
const originalFetch=window.fetch.bind(window);
// Downloads and streamed replies use fetch directly, so they share the same session.
window.fetch=(resource,init={})=>{
  const url=new URL(resource instanceof Request?resource.url:resource,location.href);
  const localAPI=url.origin===new URL(accountAPI).origin;
  return originalFetch(resource,localAPI?{...init,credentials:'include'}:init).then(response=>{
    if(localAPI&&response.status===401&&url.pathname!=='/api/auth/login'&&!(loginDialog.open&&loginRequired)){
      document.documentElement.classList.add('auth-pending');showAccountLogin('',true);
    }
    return response;
  });
};
let finishAccountReady;
window.workspaceReady=new Promise(resolve=>{finishAccountReady=resolve;});
document.documentElement.classList.add('auth-pending');
const loginDialog=document.createElement('dialog');
loginDialog.id='account-login';loginDialog.className='account-login';
loginDialog.innerHTML=`<form id="account-login-form"><div class="login-brand"><span class="brand-icon">电</span><span>项目评审</span></div><h1>登录工作台</h1><label class="form-field">账号<input name="username" autocomplete="username" required maxlength="80" placeholder="请输入账号"></label><label class="form-field">密码<input name="password" type="password" autocomplete="current-password" required placeholder="请输入密码"></label><p class="form-error" role="alert"></p><button class="primary login-submit" type="submit">登录</button><button class="subtle login-cancel" type="button">取消切换</button></form>`;
document.body.append(loginDialog);
let loginRequired=true;
function showAccountLogin(username='',required=false){
  loginRequired=required;
  loginDialog.querySelector('[name="username"]').value=username;
  loginDialog.querySelector('[name="password"]').value='';
  loginDialog.querySelector('.form-error').textContent='';
  loginDialog.querySelector('.login-cancel').hidden=required;
  if(!loginDialog.open)loginDialog.showModal();
  loginDialog.querySelector(username?'[name="password"]':'[name="username"]').focus();
}
loginDialog.addEventListener('cancel',event=>{if(loginRequired)event.preventDefault();});
loginDialog.querySelector('.login-cancel').addEventListener('click',()=>loginDialog.close());
function clearAccountSelection(){
  for(const key of ['review-workbench-project','review-workbench-selected-report','review-workbench-account'])localStorage.removeItem(key);
}
loginDialog.querySelector('form').addEventListener('submit',async event=>{
  event.preventDefault();event.stopPropagation();
  const form=event.target,submit=form.querySelector('[type="submit"]');submit.disabled=true;
  try{
    const response=await fetch(`${accountAPI}/api/auth/login`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(Object.fromEntries(new FormData(form)))});
    const data=await response.json();if(!response.ok)throw new Error(data.detail||'登录失败，请重试。');
    clearAccountSelection();
    const destination=new URL(`${data.role==='admin'?'admin.html':'index.html'}#overview`,location.href);
    // A fragment-only navigation keeps the pending login state in the same document.
    if(destination.pathname===location.pathname){history.replaceState(null,'',destination.href);location.reload();}
    else location.replace(destination.href);
  }catch(error){form.querySelector('.form-error').textContent=error.message==='Failed to fetch'?'无法连接服务，请稍后重试。':error.message;}
  finally{submit.disabled=false;}
});
async function verifyAccount(){
  try{
    const response=await fetch(`${accountAPI}/api/auth/me`);
    if(!response.ok){showAccountLogin('',true);return;}
    const account=await response.json();window.currentAccount=account;
    if(account.role!==document.body.dataset.workspace){location.replace(`${account.role==='admin'?'admin.html':'index.html'}#overview`);return;}
    localStorage.setItem('review-workbench-account',account.username);
    document.querySelectorAll('[data-account-switch]').forEach(select=>{
      select.innerHTML=`<option value="user1">user1 · 用户</option><option value="manner">manner · 管理员</option><option value="logout">退出登录</option>`;
      select.value=account.username;
      select.addEventListener('change',async()=>{
        const selected=select.value;select.value=account.username;
        if(selected==='logout'){
          const response=await fetch(`${accountAPI}/api/auth/logout`,{method:'POST'});
          if(response.ok){clearAccountSelection();location.reload();}
        }else showAccountLogin(selected,false);
      });
    });
    document.querySelectorAll('[data-account-avatar]').forEach(avatar=>avatar.textContent=account.role==='admin'?'管':'用');
    document.documentElement.classList.remove('auth-pending');finishAccountReady(account);
  }catch(error){showAccountLogin('',true);loginDialog.querySelector('.form-error').textContent='无法连接服务，请确认前后端已启动。';}
}
verifyAccount();
window.addEventListener('storage',event=>{
  if(event.key==='review-workbench-account'&&window.currentAccount&&event.newValue!==window.currentAccount.username)location.reload();
});
