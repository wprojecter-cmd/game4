import * as Cesium from "cesium";
import { spawnExplosionVfx } from "./ExplosionVfx.js";

const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));

export function startDroneFlight(viewer,startPosition,onCollision=()=>{}){
  if(!viewer||viewer.isDestroyed()||!startPosition)return()=>{};
  const scene=viewer.scene,camera=viewer.camera,ellipsoid=scene.globe.ellipsoid;
  const start=Cesium.Cartographic.fromCartesian(startPosition);
  const position=new Cesium.Cartographic(start.longitude,start.latitude,Math.max(20,start.height+20));
  let heading=0,speed=0,verticalSpeed=0,disposed=false,crashed=false,lastTime=performance.now(),frame=0,lastCollisionCheck=0;
  const keys=Object.create(null),droneEntities=[];
  const color=Cesium.Color.fromCssColorString("#c9ff55"),dark=Cesium.Color.fromCssColorString("#252c35");
  const worldPosition=()=>Cesium.Cartesian3.fromRadians(position.longitude,position.latitude,position.height,ellipsoid);
  function localPoint(east,north,up){
    const transform=Cesium.Transforms.eastNorthUpToFixedFrame(worldPosition());
    return Cesium.Matrix4.multiplyByPoint(transform,new Cesium.Cartesian3(east,north,up),new Cesium.Cartesian3());
  }
  const body=viewer.entities.add({position:worldPosition(),ellipsoid:{radii:new Cesium.Cartesian3(.65,.65,.28),material:dark,outline:true,outlineColor:color}});
  droneEntities.push(body);
  const arms=[[1.5,1.5],[-1.5,1.5],[1.5,-1.5],[-1.5,-1.5]],rotors=[];
  for(const [x,y] of arms){
    const arm=viewer.entities.add({polyline:{positions:[],width:5,material:color}});
    droneEntities.push(arm);
    const rotor=viewer.entities.add({position:worldPosition(),ellipsoid:{radii:new Cesium.Cartesian3(.68,.68,.07),material:Cesium.Color.fromCssColorString("#647080"),outline:true,outlineColor:color}});
    droneEntities.push(rotor);rotors.push({x,y,arm,rotor});
  }
  function updateModel(){
    const center=worldPosition();body.position=center;
    for(const item of rotors){
      const east=item.x*Math.cos(heading)+item.y*Math.sin(heading),north=-item.x*Math.sin(heading)+item.y*Math.cos(heading);
      const rotorPosition=localPoint(east,north,.05);item.rotor.position=rotorPosition;item.arm.polyline.positions=[center,rotorPosition];
    }
  }
  function updateCamera(){
    const cameraPosition=localPoint(-Math.sin(heading)*10,-Math.cos(heading)*10,4.5),target=localPoint(0,0,1);
    const direction=Cesium.Cartesian3.normalize(Cesium.Cartesian3.subtract(target,cameraPosition,new Cesium.Cartesian3()),new Cesium.Cartesian3());
    const enu=Cesium.Transforms.eastNorthUpToFixedFrame(worldPosition());
    const up=Cesium.Cartesian3.normalize(Cesium.Matrix4.multiplyByPointAsVector(enu,Cesium.Cartesian3.UNIT_Z,new Cesium.Cartesian3()),new Cesium.Cartesian3());
    camera.setView({destination:cameraPosition,orientation:{direction,up}});
  }
  function removeDrone(){for(const entity of droneEntities)viewer.entities.remove(entity);}
  function cleanup(){
    if(disposed)return;disposed=true;cancelAnimationFrame(frame);
    window.removeEventListener("keydown",keyDown);window.removeEventListener("keyup",keyUp);window.removeEventListener("blur",clearKeys);
    if(!viewer.isDestroyed()){removeDrone();const c=scene.screenSpaceCameraController;c.enableRotate=true;c.enableTranslate=true;c.enableZoom=true;c.enableTilt=true;c.enableLook=true;}
  }
  function keyDown(event){
    keys[event.code]=true;
    if(["KeyW","KeyA","KeyS","KeyD","ArrowUp","ArrowDown","ArrowLeft","ArrowRight","Space","ControlLeft","ControlRight","ShiftLeft","ShiftRight"].includes(event.code))event.preventDefault();
  }
  function keyUp(event){keys[event.code]=false;}
  function clearKeys(){for(const key of Object.keys(keys))keys[key]=false;}
  function collide(){
    if(crashed||disposed)return;crashed=true;const impact=worldPosition();cleanup();
    try{spawnExplosionVfx(viewer,impact,22);}catch(error){console.error("Drone explosion failed:",error);}
    onCollision();
  }
  function checkCollision(now){
    if(now-lastCollisionCheck<100)return;lastCollisionCheck=now;
    const terrain=scene.globe.getHeight(position);let surface=terrain;
    try{const sampled=scene.sampleHeight(position,droneEntities,2);if(Number.isFinite(sampled))surface=Math.max(surface??-Infinity,sampled);}catch{}
    if(Number.isFinite(surface)&&position.height<=surface+1.4)collide();
  }
  function update(now){
    if(disposed||viewer.isDestroyed()){cleanup();return;}
    const dt=Math.min(.05,Math.max(0,(now-lastTime)/1000));lastTime=now;
    const forward=keys.KeyW||keys.ArrowUp,backward=keys.KeyS||keys.ArrowDown,left=keys.KeyA||keys.ArrowLeft,right=keys.KeyD||keys.ArrowRight;
    const ascending=keys.Space,descending=keys.ControlLeft||keys.ControlRight,boost=keys.ShiftLeft||keys.ShiftRight;
    if(left)heading-=1.8*dt;if(right)heading+=1.8*dt;
    const maxSpeed=boost?65:28;
    if(forward)speed+=24*dt;else if(backward)speed-=24*dt;else speed*=Math.max(0,1-2.2*dt);
    speed=clamp(speed,-maxSpeed*.45,maxSpeed);
    if(ascending)verticalSpeed+=16*dt;else if(descending)verticalSpeed-=16*dt;else verticalSpeed*=Math.max(0,1-2.5*dt);
    verticalSpeed=clamp(verticalSpeed,-18,18);
    const distance=speed*dt,east=Math.sin(heading)*distance,north=Math.cos(heading)*distance,radius=ellipsoid.maximumRadius,cosLat=Math.max(.05,Math.cos(position.latitude));
    position.longitude+=east/(radius*cosLat);position.latitude=clamp(position.latitude+north/radius,-Math.PI/2+.001,Math.PI/2-.001);position.height=Math.max(1,position.height+verticalSpeed*dt);
    updateModel();updateCamera();checkCollision(now);
    if(!disposed){scene.requestRender();frame=requestAnimationFrame(update);}
  }
  const controller=scene.screenSpaceCameraController;
  controller.enableRotate=false;controller.enableTranslate=false;controller.enableZoom=false;controller.enableTilt=false;controller.enableLook=false;
  window.addEventListener("keydown",keyDown,{passive:false});window.addEventListener("keyup",keyUp);window.addEventListener("blur",clearKeys);
  updateModel();updateCamera();frame=requestAnimationFrame(update);
  return cleanup;
}
