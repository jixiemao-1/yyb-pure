const axios = require('axios');
const fs = require('fs');
const path = require('path');
const { execFile } = require('child_process');

const APPID = "wxed3cf95a14b58a26";
const PYTHON_BIN = process.env.PYTHON_BIN || "python";
const GET_CODE_PY = path.join(__dirname, 'getCode.py');


// ================= 配置常量 =================
const FILE = path.join(__dirname, 'nwdjgcookie.json');
const BASE = 'https://stdcrm.dtmiller.com';

const LOGIN_API = '/std-weixin-mp-service/miniApp/custom/login';
const USER_API = '/scrm-promotion-service/mini/wly/user/info';
const SIGN_API = '/scrm-promotion-service/promotion/sign/today';
const CONFIG_API = '/scrm-promotion-service/mini/index/base/config';


// ================= UA =================
const UA_LIST = [
  "Mozilla/5.0 (Linux; Android 15) MicroMessenger/8.0.71",
  "Mozilla/5.0 (Linux; Android 14) MicroMessenger/8.0.70"
];

const UA = () => UA_LIST[Math.floor(Math.random()*UA_LIST.length)];


// ================= utils =================
const sleep = ms => new Promise(r=>setTimeout(r,ms));
const PY_JSON_MARK = "__NWDJG_JSON__";


// 文件锁（防止多端口同时写）
let fileLock = false;


function load(){
  try{
    if(fs.existsSync(FILE)){
      return JSON.parse(fs.readFileSync(FILE,'utf8'));
    }
  }catch(e){}

  return {};
}


async function save(d){

  while(fileLock)
    await sleep(50);

  fileLock=true;

  try{
    fs.writeFileSync(
      FILE,
      JSON.stringify(d,null,2)
    );
  }
  finally{
    fileLock=false;
  }
}



// ================= init =================

function init(cache){

  if(!cache._global){

    cache._global={

      promotionId:null,

      loaded:false,

      lastConfigTime:0

    };

  }

}


// ================= random jitter =================

function jitter(base){

  return base + Math.random()*base;

}



function runGetCodePy(payload, timeout = 90000){

  const pyCode = `
import importlib.util
import json
import os
import pathlib
import sys

module_path = pathlib.Path(sys.argv[1])
payload = json.loads(sys.argv[2])
spec = importlib.util.spec_from_file_location("getCode", module_path)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

getter = mod.make_getter(os.getenv("WECHAT_SERVER") or "", os.getenv("LICENSE_KEY") or os.getenv("AUTH") or os.getenv("ADMIN_KEY") or "")
if os.getenv("WX_ID"):
    getter.wx_id_filter = os.getenv("WX_ID")
    getter.target_wx_ids = [x.strip() for x in getter.wx_id_filter.split("&") if x.strip()]

if payload.get("action") == "accounts":
    rows = []
    for idx, (account, _) in enumerate(getter.get_online_accounts(), 1):
        wxid = account.get("openid") or account.get("wxid") or account.get("wx_id") or ""
        nick = account.get("nickname") or account.get("nick_name") or ""
        if not nick.strip() or nick.strip() == "ㅤ":
            nick = ("账号_" + wxid[-6:]) if wxid else ("账号_" + str(idx))
        if wxid:
            rows.append({"id": wxid, "wxid": wxid, "nickname": nick, "license": wxid})
    print("${PY_JSON_MARK}" + json.dumps({"ok": True, "protocol": "yyb呆呆", "accounts": rows}, ensure_ascii=False))
elif payload.get("action") == "code":
    wxid = payload.get("wxid") or payload.get("id")
    if not wxid:
        raise RuntimeError("缺少 wxid")
    code = getter.get_applet_code(payload.get("appId"), wxid)
    print("${PY_JSON_MARK}" + json.dumps({"ok": True, "protocol": "yyb呆呆", "code": code}, ensure_ascii=False))
else:
    raise RuntimeError("未知 action")
`;

  return new Promise((resolve, reject)=>{
    execFile(
      PYTHON_BIN,
      ['-c', pyCode, GET_CODE_PY, JSON.stringify(payload)],
      {
        cwd: __dirname,
        timeout,
        windowsHide: true
      },
      (error, stdout, stderr)=>{
        if(error){
          reject(new Error(`${error.message}${stderr ? `；${stderr.trim()}` : ''}`));
          return;
        }

        const line = String(stdout || '')
          .split(/\r?\n/)
          .reverse()
          .find(x=>x.startsWith(PY_JSON_MARK));

        if(!line){
          reject(new Error(`getCode.py 未返回JSON结果${stderr ? `：${stderr.trim()}` : ''}`));
          return;
        }

        try{
          resolve(JSON.parse(line.slice(PY_JSON_MARK.length)));
        }catch(e){
          reject(new Error(`解析getCode.py结果失败: ${e.message}`));
        }
      }
    );
  });

}


// ==================================================
// getCode.py 牛子协议获取账号列表
// ==================================================

async function getWxAccounts(){

  try{

    const res = await runGetCodePy({
      action: "accounts"
    });

    return res.accounts || [];

  }catch(e){

    console.log(
      `❌ getCode.py牛子协议获取账号失败: ${e.message}`
    );

  }


  return [];

}



// ==================================================
// getCode.py 牛子协议获取小程序code
// ==================================================

async function getCode(account){

  try{

    const res = await runGetCodePy({
      action: "code",
      appId: APPID,
      wxid: account.wxid || account.id
    });

    return res.code || null;

  }catch(e){

    console.log(
      `❌ [wxid:${account.id}] 获取Code失败: ${e.message}`
    );

  }


  return null;

}
// ================= login =================
// 保留原业务登录逻辑
// code来源改为getCode.py牛子协议

async function login(account, ua){

  let code = await getCode(account);

  if(!code)
    return null;


  try{

    let res = await axios.post(

      BASE + LOGIN_API,

      {

        code,

        appId:APPID

      },

      {

        headers:{
          'User-Agent':ua
        },

        timeout:10000

      }

    );


    return res.data?.data?.access_token
        || res.data?.data?.token
        || res.data?.data;


  }catch(e){}


  return null;

}



// ================= user（Token有效性校验） =================

async function userCheck(token, ua){

  try{


    let r = await axios.get(

      BASE + USER_API,

      {

        headers:{

          Authorization:`Bearer ${token}`,

          'User-Agent':ua

        },

        timeout:10000

      }

    );


    return r.data?.code===0
      ? r.data.data
      : null;


  }catch(e){}


  return null;

}




// ================= token（优先缓存，失效重登） =================

async function getToken(account, cache){


  let key = String(account.id);


  let item = cache[key] || {};


  let ua = item.ua || UA();



  cache[key]=item;


  item.ua=ua;



  // 有缓存token，尝试复用

  if(item.token){


    let u = await userCheck(
      item.token,
      ua
    );


    if(u){


      console.log(
        `✅ [wxid:${account.id}] 使用缓存Token登录成功`
      );


      return {

        token:item.token,

        ua,

        user:u

      };


    }


    else{


      console.log(
        `⚠️ [wxid:${account.id}] 缓存Token失效，重新登录`
      );

      delete item.token;
      await save(cache);

    }

  }




  // code重新登录

  console.log(
    `🔑 [wxid:${account.id}] 正在通过getCode.py牛子协议获取Code登录...`
  );



  let t = await login(
    account,
    ua
  );



  if(!t)
    return null;



  let u = await userCheck(
    t,
    ua
  );



  if(!u)
    return null;



  item.token=t;


  await save(cache);



  console.log(
    `✅ [wxid:${account.id}] Code登录成功，Token已保存`
  );



  return {

    token:t,

    ua,

    user:u

  };

}





// ================= promotionId（纯缓存，无触发） =================

async function getPromotionId(cache){

  if(cache._global.promotionId){

    return cache._global.promotionId;

  }


  return null;

}






// ================= CONFIG（仅初始化一次） =================

async function initPromotionIdOnce(token, ua, cache){


  if(cache._global.loaded)
    return;



  try{


    let res = await axios.get(

      BASE + CONFIG_API,

      {

        headers:{

          Authorization:`Bearer ${token}`,

          'User-Agent':ua

        },

        timeout:10000

      }

    );



    let list=res.data?.data;



    if(!Array.isArray(list))
      return;




    for(let item of list){


      if(!item?.detailJson)
        continue;



      let parsed;


      try{

        parsed=JSON.parse(
          item.detailJson
        );

      }catch(e){

        continue;

      }




      let arr = Array.isArray(parsed)
        ? parsed
        : [parsed];





      for(let s of arr){


        let m =
          (
            s?.jumpData?.pagePath || ''
          )
          .match(
            /promotionId=([^&]+)/
          );



        if(m){


          cache._global.promotionId=m[1];


          cache._global.loaded=true;


          cache._global.lastConfigTime=Date.now();



          await save(cache);



          return;


        }


      }


    }



  }catch(e){}


}

// ================= sign（纯执行，无逻辑） =================

async function sign(account, token, ua, cache){


  let pid = cache._global.promotionId;



  if(!pid){


      console.log(
      `❌ [wxid:${account.id}] 无promotionId`
    );


    return;

  }




  try{


    await axios.get(

      `${BASE}${SIGN_API}?promotionId=${pid}`,

      {

        headers:{

          Authorization:`Bearer ${token}`,

          'User-Agent':ua

        },

        timeout:10000

      }

    );



    console.log(
      `📊 [wxid:${account.id}] 签到成功`
    );



  }catch(e){


    console.log(
      `❌ [wxid:${account.id}] 签到失败`
    );


  }

}




// ================= 获取积分 =================

async function getPoints(account, token, ua){


  try{


    let res = await axios.get(

      BASE + USER_API,

      {

        headers:{

          Authorization:`Bearer ${token}`,

          'User-Agent':ua

        },

        timeout:10000

      }

    );



    let points =
      res.data?.data?.member?.points || 0;



    console.log(
      `💰 [wxid:${account.id}] 总积分：${points}`
    );



  }catch(e){


    console.log(
      `❌ [wxid:${account.id}] 获取积分失败`
    );


  }

}




// ================= main =================

(async()=>{


  console.log(
    "===== 浓五的酒馆签到 ====="
  );



  // 获取getCode.py牛子协议账号池

  let accounts = await getWxAccounts();



  if(accounts.length===0){


    console.log(
      "❌ getCode.py牛子协议没有可用账号"
    );


    process.exit(1);

  }



  console.log(
    `📋 getCode.py牛子协议获取账号数量: ${accounts.length}`
  );



  let cache = load();


  init(cache);





  for(let account of accounts){



    await sleep(
      jitter(1200)
    );




    let res = await getToken(
      account,
      cache
    );



    if(!res)
      continue;




    let {
      token,
      ua,
      user
    } = res;





    console.log(

      `👤 [wxid:${account.id}] ${
        user?.member?.nick_name || account.nickname || '未知'
      }`

    );






    await initPromotionIdOnce(
      token,
      ua,
      cache
    );





    await sign(
      account,
      token,
      ua,
      cache
    );





    await getPoints(
      account,
      token,
      ua
    );






    await sleep(
      jitter(800)
    );



  }





  console.log(
    "===== 执行完成 ====="
  );



})();
