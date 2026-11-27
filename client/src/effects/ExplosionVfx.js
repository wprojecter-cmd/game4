import * as Cesium from "cesium";

export function spawnExplosionVfx(viewer,worldPosition,size=18){
  if(!viewer||viewer.isDestroyed()||!worldPosition)return()=>{};
  const scene=viewer.scene,start=performance.now(),duration=1250,center=Cesium.Cartesian3.clone(worldPosition);
  const frame=Cesium.Transforms.eastNorthUpToFixedFrame(center),entities=[];
  const local=(x,y,z)=>Cesium.Matrix4.multiplyByPoint(frame,new Cesium.Cartesian3(x,y,z),new Cesium.Cartesian3());
  const fire=viewer.entities.add({position:center,ellipsoid:{radii:new Cesium.Cartesian3(1,1,1),material:Cesium.Color.ORANGE.withAlpha(.85),outline:false}});
  entities.push(fire);
  const sparks=[];
  for(let i=0;i<22;i++){
    const a=Math.random()*Math.PI*2,elevation=(Math.random()-.2)*1.4,speed=size*(.55+Math.random()*.8);
    const spark=viewer.entities.add({position:center,point:{pixelSize:5+Math.random()*4,color:i%3?Cesium.Color.ORANGE:Cesium.Color.YELLOW,outlineColor:Cesium.Color.WHITE,outlineWidth:1,disableDepthTestDistance:Number.POSITIVE_INFINITY}});
    entities.push(spark);sparks.push({entity:spark,a,elevation,speed});
  }
  let raf=0,disposed=false;
  const remove=()=>{if(disposed)return;disposed=true;cancelAnimationFrame(raf);if(!viewer.isDestroyed())for(const entity of entities)viewer.entities.remove(entity);};
  const tick=now=>{
    if(disposed||viewer.isDestroyed()){remove();return;}
    const t=Math.min(1,(now-start)/duration),ease=1-Math.pow(1-t,3),radius=Math.max(.1,size*ease);
    fire.ellipsoid.radii=new Cesium.Cartesian3(radius,radius,radius*.78);
    fire.ellipsoid.material=Cesium.Color.fromAlpha(Cesium.Color.lerp(Cesium.Color.YELLOW,Cesium.Color.DARKRED,t,new Cesium.Color()),Math.max(0,.85*(1-t)));
    for(const p of sparks){
      const d=p.speed*t,flat=Math.cos(p.elevation)*d,up=Math.sin(p.elevation)*d+size*.25*t;
      p.entity.position=local(Math.cos(p.a)*flat,Math.sin(p.a)*flat,up);
      p.entity.point.color=Cesium.Color.fromAlpha(Cesium.Color.lerp(Cesium.Color.YELLOW,Cesium.Color.DARKRED,t,new Cesium.Color()),Math.max(0,1-t));
      p.entity.point.pixelSize=Math.max(1,6*(1-t));
    }
    scene.requestRender();
    if(t<1)raf=requestAnimationFrame(tick);else remove();
  };
  raf=requestAnimationFrame(tick);return remove;
}
