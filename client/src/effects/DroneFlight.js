import * as Cesium from "cesium";
import { spawnExplosionVfx } from "./ExplosionVfx.js";

const clamp=(v,a,b)=>Math.max(a,Math.min(b,v));

export function startDroneFlight(viewer,startPosition,onCollision=()=>{},onCameraModeChange=()=>{}){
  if(!viewer||viewer.isDestroyed()||!startPosition)return()=>{};
  const scene=viewer.scene,camera=viewer.camera,ellipsoid=scene.globe.ellipsoid;
  const start=Cesium.Cartographic.fromCartesian(startPosition);
  const position=new Cesium.Cartographic(start.longitude,start.latitude,Math.max(35,start.height+25));
  let heading=Cesium.Math.toRadians(0),speed=0,verticalSpeed=0,disposed=false,crashed=false;
  let lastTime=performance.now(),lastCollisionCheck=0,frame=0,cameraMode="orbit",orbitYaw=0,orbitPitch=Cesium.Math.toRadians(22),orbitDistance=18,dragging=false,lastPointer=null;
  const keys=Object.create(null),entities=[];
  const neon=Cesium.Color.fromCssColorString("#c9ff55"),bodyColor=Cesium.Color.fromCssColorString("#202a38");
  const world=()=>Cesium.Cartesian3.fromRadians(position.longitude,position.latitude,position.height,ellipsoid);
  const enu=()=>Cesium.Transforms.eastNorthUpToFixedFrame(world());
  const local=(east,north,up)=>Cesium.Matrix4.multiplyByPoint(enu(),new Cesium.Cartesian3(east,north,up),new Cesium.Cartesian3());
  const add=e=>{entities.push(viewer.entities.add(e));return entities[entities.length-1];};

  const body=add({position:world(),ellipsoid:{radii:new Cesium.Cartesian3(1.05,.72,.34),material:bodyColor,outline:true,outlineColor:neon,outlineWidth:2}});
  const nose=add({position:world(),ellipsoid:{radii:new Cesium.Cartesian3(.23,.23,.23),material:neon}});
  const arms=[];
  for(const [x,y] of [[1.85,1.35],[-1.85,1.35],[1.85,-1.35],[-1.85,-1.35]]){
    const arm=add({polyline:{positions:[],width:9,material:neon}});
    const rotor=add({position:world(),ellipsoid:{radii:new Cesium.Cartesian3(.78,.78,.11),material:Cesium.Color.fromCssColorString("#687789"),outline:true,outlineColor:neon,outlineWidth:2}});
    const hub=add({position:world(),ellipsoid:{radii:new Cesium.Cartesian3(.18,.18,.2),material:Cesium.Color.WHITE}});
    arms.push({x,y,arm,rotor,hub});
  }
  add({position:world(),point:{pixelSize:11,color:neon,outlineColor:Cesium.Color.WHITE,outlineWidth:2,disableDepthTestDistance:Number.POSITIVE_INFINITY}});
  const savedController={enableRotate:scene.screenSpaceCameraController.enableRotate,enableTranslate:scene.screenSpaceCameraController.enableTranslate,enableZoom:scene.screenSpaceCameraController.enableZoom,enableTilt:scene.screenSpaceCameraController.enableTilt,enableLook:scene.screenSpaceCameraController.enableLook};

  function updateModel(){
    const center=world();body.position=center;nose.position=local(0,1.12,.02);
    for(const a of arms){
      const east=a.x*Math.cos(heading)+a.y*Math.sin(heading),north=-a.x*Math.sin(heading)+a.y*Math.cos(heading);
      const p=local(east,north,.04);a.rotor.position=p;a.hub.position=p;a.arm.polyline.positions=[center,p];
    }
  }
  function cameraOrientation(destination,target){
    const direction=Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(target,destination,new Cesium.Cartesian3()),new Cesium.Cartesian3());
    const up=Cesium.Cartesian3.normalize(Cesium.Matrix4.multiplyByPointAsVector(enu(),Cesium.Cartesian3.UNIT_Z,new Cesium.Cartesian3()),new Cesium.Cartesian3());
    camera.setView({destination,orientation:{direction,up}});
  }
  function updateCamera(){
    let destination,target;
    if(cameraMode==="fpv"){
      destination=local(0,1.25,.48);
      target=local(Math.sin(heading)*100,Math.cos(heading)*100,.25);
    }else{
      const angle=heading+orbitYaw;
      const horizontal=orbitDistance*Math.cos(orbitPitch);
      destination=local(-Math.sin(angle)*horizontal,-Math.cos(angle)*horizontal,orbitDistance*Math.sin(orbitPitch)+1.8);
      target=local(0,0,.25);
    }
    cameraOrientation(destination,target);
  }
  function setCameraMode(mode){
    if(mode!=="fpv"&&mode!=="orbit")return;
    cameraMode=mode;dragging=false;lastPointer=null;onCameraModeChange(mode);updateCamera();scene.requestRender();
  }
  function removeEntities(){for(const entity of entities)viewer.entities.remove(entity);}
  function cleanup(){
    if(disposed)return;disposed=true;cancelAnimationFrame(frame);
    window.removeEventListener("keydown",keyDown);window.removeEventListener("keyup",keyUp);window.removeEventListener("blur",clearKeys);
    scene.canvas.removeEventListener("pointerdown",pointerDown);window.removeEventListener("pointermove",pointerMove);window.removeEventListener("pointerup",pointerUp);scene.canvas.removeEventListener("wheel",wheel);
    if(!viewer.isDestroyed()){removeEntities();const c=scene.screenSpaceCameraController;Object.assign(c,savedController);}
  }
  cleanup.setCameraMode=setCameraMode;
  function keyDown(e){
    keys[e.code]=true;
    if(e.code==="KeyV"){if(!e.repeat)setCameraMode(cameraMode==="orbit"?"fpv":"orbit");}
    if(["KeyW","KeyA","KeyS","KeyD","ArrowUp","ArrowDown","ArrowLeft","ArrowRight","Space","ControlLeft","ControlRight","ShiftLeft","ShiftRight"].includes(e.code))e.preventDefault();
  }
  function keyUp(e){keys[e.code]=false;}
  function clearKeys(){for(const k of Object.keys(keys))keys[k]=false;}
  function pointerDown(e){if(cameraMode!=="orbit"||e.button!==0)return;dragging=true;lastPointer={x:e.clientX,y:e.clientY};}
  function pointerMove(e){
    if(!dragging||!lastPointer||cameraMode!=="orbit")return;
    const dx=e.clientX-lastPointer.x,dy=e.clientY-lastPointer.y;lastPointer={x:e.clientX,y:e.clientY};
    orbitYaw-=dx*.006;orbitPitch=clamp(orbitPitch+dy*.004,Cesium.Math.toRadians(-5),Cesium.Math.toRadians(78));updateCamera();
  }
  function pointerUp(){dragging=false;lastPointer=null;}
  function wheel(e){if(cameraMode!=="orbit")return;e.preventDefault();orbitDistance=clamp(orbitDistance+Math.sign(e.deltaY)*Math.max(1,orbitDistance*.12),5,90);updateCamera();}
  function collide(){
    if(crashed||disposed)return;crashed=true;const impact=world();cleanup();
    try{spawnExplosionVfx(viewer,impact,22);}catch(error){console.error("Drone explosion failed:",error);}
    onCollision();
  }
  function checkCollision(now){
    if(now-lastCollisionCheck<120)return;lastCollisionCheck=now;
    let surface=scene.globe.getHeight(position);
    try{const sampled=scene.sampleHeight(position,entities,2);if(Number.isFinite(sampled))surface=Math.max(surface??-Infinity,sampled);}catch{}
    if(Number.isFinite(surface)&&position.height<=surface+1.7)collide();
  }
  function update(now){
    if(disposed||viewer.isDestroyed()){cleanup();return;}
    const dt=Math.min(.05,Math.max(0,(now-lastTime)/1000));lastTime=now;
    const forward=keys.KeyW||keys.ArrowUp,back=keys.KeyS||keys.ArrowDown,left=keys.KeyA||keys.ArrowLeft,right=keys.KeyD||keys.ArrowRight;
    if(left)heading-=1.8*dt;if(right)heading+=1.8*dt;
    const boost=keys.ShiftLeft||keys.ShiftRight,maxSpeed=boost?65:28;
    if(forward)speed+=24*dt;else if(back)speed-=24*dt;else speed*=Math.max(0,1-2.2*dt);
    speed=clamp(speed,-maxSpeed*.45,maxSpeed);
    if(keys.Space)verticalSpeed+=16*dt;else if(keys.ControlLeft||keys.ControlRight)verticalSpeed-=16*dt;else verticalSpeed*=Math.max(0,1-2.5*dt);
    verticalSpeed=clamp(verticalSpeed,-18,18);
    const distance=speed*dt,east=Math.sin(heading)*distance,north=Math.cos(heading)*distance,radius=ellipsoid.maximumRadius,cosLat=Math.max(.05,Math.cos(position.latitude));
    position.longitude+=east/(radius*cosLat);position.latitude=clamp(position.latitude+north/radius,-Math.PI/2+.001,Math.PI/2-.001);position.height=Math.max(2,position.height+verticalSpeed*dt);
    updateModel();updateCamera();checkCollision(now);
    if(!disposed){scene.requestRender();frame=requestAnimationFrame(update);}
  }
  const controller=scene.screenSpaceCameraController;
  controller.enableRotate=false;controller.enableTranslate=false;controller.enableZoom=false;controller.enableTilt=false;controller.enableLook=false;
  window.addEventListener("keydown",keyDown,{passive:false});window.addEventListener("keyup",keyUp);window.addEventListener("blur",clearKeys);
  scene.canvas.addEventListener("pointerdown",pointerDown);window.addEventListener("pointermove",pointerMove);window.addEventListener("pointerup",pointerUp);scene.canvas.addEventListener("wheel",wheel,{passive:false});
  updateModel();updateCamera();frame=requestAnimationFrame(update);
  return cleanup;
}
