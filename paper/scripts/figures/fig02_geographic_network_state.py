from style import *
from data import *
import geopandas as gpd
import pandapower as pp
from matplotlib.lines import Line2D
from matplotlib.colors import Normalize,TwoSlopeNorm

def build():
    base='portuguese_hv_network/site/public/data/pt60/'
    lines=gpd.read_file(source(base+'lines.geojson')).to_crs(3763)
    buses=gpd.read_file(source(base+'buses.geojson')).to_crs(3763)
    boundary=gpd.read_file(source('paper/figures/pt60_review/portugal_gisco_2024.geojson')).to_crs(3763).explode(index_parts=False)
    boundary=boundary.iloc[[int(np.argmax(boundary.geometry.area.to_numpy()))]]
    net=pp.from_json(str(source('portuguese_hv_network/outputs/annual_nminus1_panel_v2/models/PT60_ANNUAL_PEAK_LOAD_20260115_1215_+0000_solved.json')))
    assert len(lines)==len(net.line)==4943 and len(buses)==len(net.bus)==3783
    assert lines.id.is_unique and buses.id.is_unique
    assert set(lines.id)==set(net.line.line_id) and set(buses.id)==set(net.bus.bus_id)
    lm=pd.Series(net.res_line.loading_percent.to_numpy(),index=net.line.line_id)
    bm=pd.Series(net.res_bus.vm_pu.to_numpy(),index=net.bus.bus_id)
    lines['loading']=lines.id.map(lm);buses['vm']=buses.id.map(bm)
    fig,axs=plt.subplots(1,3,figsize=(7.2,5.8))
    fig.subplots_adjust(left=.025,right=.985,top=.95,bottom=.16,wspace=.08)
    b=boundary.total_bounds;lb=lines.total_bounds
    for ax,letter,label in zip(axs,'abc',['Network','Line loading','Bus voltage']):
        boundary.boundary.plot(ax=ax,color='#BBBBBB',lw=.35,zorder=0)
        ax.set(xlim=(min(b[0],lb[0])-9000,max(b[2],lb[2])+9000),ylim=(b[1]-10000,b[3]+9000))
        ax.set_aspect('equal');ax.axis('off');panel(ax,letter,label)
    for v,c in VC.items():
        lines[lines.voltage_kv==v].plot(ax=axs[0],color=c,lw=.22 if v<150 else .48,alpha=.8)
    assert lines.voltage_kv.isin(VC).all()
    fig.legend(handles=[Line2D([0],[0],color=c,lw=1.3,label=f'{v} kV') for v,c in VC.items()],loc='upper left',fontsize=7,bbox_to_anchor=(.025,.16),ncol=2,columnspacing=1.1)
    vmax=max(120,float(np.ceil(lines.loading.max()/20)*20));norm=Normalize(0,vmax)
    lines.plot(ax=axs[1],color=LIGHT,lw=.22)
    lines.dropna(subset=['loading']).plot(ax=axs[1],column='loading',cmap='cividis',norm=norm,lw=.48)
    lines.plot(ax=axs[2],color='#D8D8D8',lw=.20,zorder=1)
    q=buses.dropna(subset=['vm']);vn=Normalize(vmin=min(.9,q.vm.min()),vmax=max(1.1,q.vm.max()))
    sc=axs[2].scatter(q.geometry.x,q.geometry.y,c=q.vm,cmap='PuBuGn',norm=vn,s=1.9,lw=0,zorder=2)
    missing=buses[buses.vm.isna()]
    if len(missing):axs[2].scatter(missing.geometry.x,missing.geometry.y,s=1.2,c=GRAY,lw=0,zorder=3)
    for ax,mappable,label in [(axs[1],plt.cm.ScalarMappable(norm=norm,cmap='cividis'),'Loading (%)'),(axs[2],sc,'Voltage (p.u.)')]:
        p=ax.get_position();ca=fig.add_axes([p.x0+.02,.105,p.width-.04,.015]);fig.colorbar(mappable,cax=ca,orientation='horizontal').set_label(label)
    ax=axs[0];x=b[0]+18000;y=b[1]-2000
    ax.plot([x,x+100000],[y,y],color=BLACK,lw=1.2);ax.text(x+50000,y+16000,'100 km',ha='center',fontsize=7)
    save(fig,2,{'crs':'EPSG:3763','buses':len(buses),'lines':len(lines),'missing_line_states':int(lines.loading.isna().sum()),'missing_bus_states':int(buses.vm.isna().sum()),'load_mw':float(net.load.p_mw.sum())})

if __name__=='__main__':setup();build()
