# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingParameterRasterLayer,
    QgsProcessingParameterFolderDestination, QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean, QgsProcessingParameterEnum, QgsProcessingOutputFolder,
    QgsProcessingException
)
from osgeo import gdal
import os
import numpy as np
from .utils import (
    read_raster, write_raster, normalise, array_stats, p_label,
    write_stats_csv, polygonize_class_raster, ensure_dir, load_layer
)


class CombinedAnalysisAnomalyExtraction(QgsProcessingAlgorithm):
    """Generate complementary radiometric indices and extract major anomalies.

    A. RRAI-U / Uranium Enrichment Index:
       0.4(U/Th)norm + 0.4(U/K)norm + 0.2[1 - (K/Th)norm]

    B. HRAI-KU / Hybrid Alteration-Source Index:
       0.4(U/Th)norm + 0.4(K/U)norm + 0.2[1 - (K/Th)norm]

    If K/U is not supplied, the algorithm still generates RRAI-U and HRI. HRI does not require K/U.
    """
    WORKFLOW_STEP_ID = 'step_04_combined_analysis'
    UTH='UTH_RATIO'; UK='UK_RATIO'; KU='KU_RATIO'; KTH='KTH_RATIO'; OUT='OUTPUT_FOLDER'
    R_MODE='RRAI_MODE'; H_MODE='HRAI_MODE'; RWU='RRAI_W_UTH'; RWK='RRAI_W_SECOND'; RWKT='RRAI_W_KTH'; HWU='HRAI_W_UTH'; HWK='HRAI_W_SECOND'; HWKT='HRAI_W_KTH'; HRIWU='HRI_W_UTH'; HRIWK='HRI_W_UK'; HRIWTHK='HRI_W_THK'
    EI='EXTRACT_INDEX'; RH='RRAI_HIGH_Q'; RV='RRAI_VHIGH_Q'; AREA='MIN_AREA_KM2'; LOAD='LOAD_OUTPUTS'; OUTPUT_FOLDER='OUTPUT_FOLDER_OUT'

    def tr(self,s): return QCoreApplication.translate('CombinedAnalysisAnomalyExtraction', s)
    def createInstance(self): return CombinedAnalysisAnomalyExtraction()
    def name(self): return '04_combined_analysis_anomaly_extraction'
    def displayName(self): return self.tr('03 - Dual Combined Indices and Anomaly Extraction')
    def group(self): return self.tr('Radiometric Analysis')
    def groupId(self): return 'radiometric_analysis'
    def shortHelpString(self):
        return self.tr('Normalises ratio rasters and creates complementary indices: RRAI-U using U/K, HRAI-KU using K/U, and HRI = w1·z(ln(U/Th)) + w2·z(ln(U/K)) - w3·z(ln(Th/K)). Each selected index is classified and polygonized using the selected high/very-high percentiles.')

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.UTH,self.tr('U/Th ratio raster')))
        self.addParameter(QgsProcessingParameterRasterLayer(self.UK,self.tr('U/K ratio raster for RRAI-U')))
        self.addParameter(QgsProcessingParameterRasterLayer(self.KU,self.tr('Optional K/U ratio raster for HRAI-KU'), optional=True))
        self.addParameter(QgsProcessingParameterRasterLayer(self.KTH,self.tr('K/Th ratio raster')))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT,self.tr('Output folder')))
        modes=['Weighted mean','Arithmetic mean','Geometric mean','Minimum consensus','Maximum response','Weighted geometric/product consensus']
        self.addParameter(QgsProcessingParameterEnum(self.R_MODE,self.tr('RRAI-U combination mode'),options=modes,defaultValue=0))
        self.addParameter(QgsProcessingParameterEnum(self.H_MODE,self.tr('HRAI-KU combination mode'),options=modes,defaultValue=0))
        self.addParameter(QgsProcessingParameterNumber(self.RWU,self.tr('RRAI-U weight for normalised U/Th'),QgsProcessingParameterNumber.Double,defaultValue=0.4,minValue=0.0,maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(self.RWK,self.tr('RRAI-U weight for normalised U/K'),QgsProcessingParameterNumber.Double,defaultValue=0.4,minValue=0.0,maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(self.RWKT,self.tr('RRAI-U weight for 1 - norm(K/Th)'),QgsProcessingParameterNumber.Double,defaultValue=0.2,minValue=0.0,maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(self.HWU,self.tr('HRAI-KU weight for normalised U/Th'),QgsProcessingParameterNumber.Double,defaultValue=0.4,minValue=0.0,maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(self.HWK,self.tr('HRAI-KU weight for normalised K/U'),QgsProcessingParameterNumber.Double,defaultValue=0.4,minValue=0.0,maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(self.HWKT,self.tr('HRAI-KU weight for 1 - norm(K/Th)'),QgsProcessingParameterNumber.Double,defaultValue=0.2,minValue=0.0,maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(self.HRIWU,self.tr('HRI weight w1 for z(ln(U/Th))'),QgsProcessingParameterNumber.Double,defaultValue=0.4,minValue=0.0,maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(self.HRIWK,self.tr('HRI weight w2 for z(ln(U/K))'),QgsProcessingParameterNumber.Double,defaultValue=0.4,minValue=0.0,maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(self.HRIWTHK,self.tr('HRI weight w3 for penalty z(ln(Th/K))'),QgsProcessingParameterNumber.Double,defaultValue=0.2,minValue=0.0,maxValue=1.0))
        self.addParameter(QgsProcessingParameterNumber(self.RH,self.tr('Combined index High percentile'),QgsProcessingParameterNumber.Double,defaultValue=85.0,minValue=0,maxValue=100))
        self.addParameter(QgsProcessingParameterNumber(self.RV,self.tr('Combined index Very High / Major anomaly percentile'),QgsProcessingParameterNumber.Double,defaultValue=95.0,minValue=0,maxValue=100))
        self.addParameter(QgsProcessingParameterEnum(self.EI,self.tr('Index to extract anomaly polygons from'),options=['RRAI-U only','HRAI-KU only','HRI only','Both RRAI-U and HRAI-KU','All RRAI-U, HRAI-KU and HRI'],defaultValue=4))
        self.addParameter(QgsProcessingParameterNumber(self.AREA,self.tr('Minimum polygon area to retain, km²'),QgsProcessingParameterNumber.Double,defaultValue=0.0,minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(self.LOAD,self.tr('Load combined outputs into QGIS'),defaultValue=True))
        self.addOutput(QgsProcessingOutputFolder(self.OUTPUT_FOLDER,self.tr('Output folder')))

    def _norm_weights(self, a, b, c, label):
        s=float(a)+float(b)+float(c)
        if s<=0:
            raise QgsProcessingException(f'{label} weights must sum to greater than zero.')
        return float(a)/s,float(b)/s,float(c)/s

    def _combine(self, n1, n2, inv, w1, w2, w3, mode):
        method=(mode or 'Weighted mean').lower()
        valid=np.isfinite(n1)&np.isfinite(n2)&np.isfinite(inv)
        out=np.full(n1.shape,np.nan,dtype=np.float64)
        stack=np.stack([n1,n2,inv],axis=0)
        if method.startswith('arithmetic'):
            out[valid]=np.nanmean(stack[:,valid],axis=0)
        elif method.startswith('geometric'):
            eps=1e-9
            out[valid]=np.exp((np.log(np.clip(n1[valid],eps,1.0))+np.log(np.clip(n2[valid],eps,1.0))+np.log(np.clip(inv[valid],eps,1.0)))/3.0)
        elif method.startswith('weighted geometric') or method.startswith('product'):
            eps=1e-9
            out[valid]=np.exp((w1*np.log(np.clip(n1[valid],eps,1.0)))+(w2*np.log(np.clip(n2[valid],eps,1.0)))+(w3*np.log(np.clip(inv[valid],eps,1.0))))
        elif method.startswith('minimum'):
            out[valid]=np.nanmin(stack[:,valid],axis=0)
        elif method.startswith('maximum'):
            out[valid]=np.nanmax(stack[:,valid],axis=0)
        else:
            out[valid]=(w1*n1[valid])+(w2*n2[valid])+(w3*inv[valid])
        return np.clip(out,0.0,1.0)


    def _zscore_log_ratio(self, arr, invert_for_th_over_k=False):
        a=np.asarray(arr,dtype=np.float64)
        valid=np.isfinite(a)&(a>0)
        out=np.full(a.shape,np.nan,dtype=np.float64)
        if not np.any(valid):
            return out
        logv=np.log(a[valid])
        if invert_for_th_over_k:
            logv=-logv
        mu=float(np.nanmean(logv)); sd=float(np.nanstd(logv))
        if sd<=0 or not np.isfinite(sd):
            out[valid]=0.0
        else:
            out[valid]=(logv-mu)/sd
        return out

    def _hri(self, UTh, UK, KTh, w1, w2, w3):
        z_uth=self._zscore_log_ratio(UTh)
        z_uk=self._zscore_log_ratio(UK)
        z_thk=self._zscore_log_ratio(KTh, invert_for_th_over_k=True)
        valid=np.isfinite(z_uth)&np.isfinite(z_uk)&np.isfinite(z_thk)
        out=np.full(UTh.shape,np.nan,dtype=np.float64)
        out[valid]=(w1*z_uth[valid])+(w2*z_uk[valid])-(w3*z_thk[valid])
        return out

    def _classify_and_polygonise(self, name, arr, profile, comb_dir, final_dir, logs, high_q, very_q, min_area, settings, load_outputs):
        stats={name:array_stats(arr,[high_q,very_q,50,75,80,85,90,95])}
        h=stats[name][f'P{p_label(high_q)}']; v=stats[name][f'P{p_label(very_q)}']
        cls=np.ones(arr.shape,dtype=np.uint8)
        cls[np.isfinite(arr)&(arr>=h)]=2
        cls[np.isfinite(arr)&(arr>=v)]=3
        cls[~np.isfinite(arr)]=0
        classp=os.path.join(comb_dir,f'{name}_class.tif')
        write_raster(classp,cls,profile,gdal.GDT_Byte,nodata=0)
        major=np.zeros(arr.shape,dtype=np.uint8)
        major[np.isfinite(arr)&(arr>=v)]=1
        majp=os.path.join(final_dir,f'{name}_Major_Anomaly.tif')
        write_raster(majp,major,profile,gdal.GDT_Byte,nodata=0)
        gp=os.path.join(final_dir,f'{name}_Major_Anomaly_polygon.gpkg')
        polygonize_class_raster(majp,gp,f'{name}_Major_Anomaly',[1],min_area)
        threshold_settings=dict(settings)
        threshold_settings.update({'High_threshold':h,'VeryHigh_Major_threshold':v,'Minimum_area_km2':min_area})
        write_stats_csv(os.path.join(logs,f'{name}_thresholds.csv'),stats,threshold_settings)
        if load_outputs:
            for p,n in [(classp,f'{name}_class'),(majp,f'{name}_Major_Anomaly'),(gp,f'{name}_Major_Anomaly_polygon')]:
                load_layer(p,n)
        return h,v,classp,majp,gp

    def processAlgorithm(self, parameters, context, feedback):
        uth_l=self.parameterAsRasterLayer(parameters,self.UTH,context)
        uk_l=self.parameterAsRasterLayer(parameters,self.UK,context)
        ku_l=self.parameterAsRasterLayer(parameters,self.KU,context)
        kth_l=self.parameterAsRasterLayer(parameters,self.KTH,context)
        out=self.parameterAsString(parameters,self.OUT,context)
        if not uth_l or not uk_l or not kth_l:
            raise QgsProcessingException('U/Th, U/K and K/Th ratio rasters are required. K/U is optional but required for the HRAI-KU index.')
        norm_dir=ensure_dir(os.path.join(out,'04_Normalised_Ratio_Maps'))
        comb_dir=ensure_dir(os.path.join(out,'08_Combined_Threshold_Maps'))
        final_dir=ensure_dir(os.path.join(out,'09_Combined_Map_Anomaly_Extraction'))
        logs=ensure_dir(os.path.join(out,'00_Stats_Logs'))
        UTh,profile=read_raster(uth_l.source()); UK,_=read_raster(uk_l.source()); KTh,_=read_raster(kth_l.source())
        KU=None
        if ku_l:
            KU,_=read_raster(ku_l.source())
        if UTh.shape!=UK.shape or UTh.shape!=KTh.shape or (KU is not None and UTh.shape!=KU.shape):
            raise QgsProcessingException('Input ratio rasters must have the same dimensions.')
        mode_items=['Weighted mean','Arithmetic mean','Geometric mean','Minimum consensus','Maximum response','Weighted geometric/product consensus']
        r_mode=mode_items[self.parameterAsEnum(parameters,self.R_MODE,context)]
        h_mode=mode_items[self.parameterAsEnum(parameters,self.H_MODE,context)]
        rw1,rw2,rw3=self._norm_weights(
            self.parameterAsDouble(parameters,self.RWU,context),
            self.parameterAsDouble(parameters,self.RWK,context),
            self.parameterAsDouble(parameters,self.RWKT,context),'RRAI-U')
        hw1,hw2,hw3=self._norm_weights(
            self.parameterAsDouble(parameters,self.HWU,context),
            self.parameterAsDouble(parameters,self.HWK,context),
            self.parameterAsDouble(parameters,self.HWKT,context),'HRAI-KU')
        hriw1,hriw2,hriw3=self._norm_weights(
            self.parameterAsDouble(parameters,self.HRIWU,context),
            self.parameterAsDouble(parameters,self.HRIWK,context),
            self.parameterAsDouble(parameters,self.HRIWTHK,context),'HRI')
        feedback.pushInfo('Normalising ratio maps and inverting K/Th favourability.')
        n_uth=normalise(UTh); n_uk=normalise(UK); n_kth=normalise(KTh); inv=1.0-n_kth; inv[~np.isfinite(n_kth)]=np.nan
        norm_outputs=[('U_Th_norm',n_uth),('U_K_norm',n_uk),('K_Th_norm',n_kth),('K_Th_inverse_norm',inv)]
        n_ku=None
        if KU is not None:
            n_ku=normalise(KU)
            norm_outputs.insert(2,('K_U_norm',n_ku))
        for name,arr in norm_outputs:
            write_raster(os.path.join(norm_dir,f'{name}.tif'),arr,profile,gdal.GDT_Float32)
        load_outputs=self.parameterAsBoolean(parameters,self.LOAD,context)
        high_q=self.parameterAsDouble(parameters,self.RH,context); very_q=self.parameterAsDouble(parameters,self.RV,context); min_area=self.parameterAsDouble(parameters,self.AREA,context); extract_idx=self.parameterAsEnum(parameters,self.EI,context)
        settings={'High_percentile':high_q,'VeryHigh_percentile':very_q}

        # A. Correct uranium enrichment index.
        rrai=self._combine(n_uth,n_uk,inv,rw1,rw2,rw3,r_mode)
        rrai[~np.isfinite(n_uth)|~np.isfinite(n_uk)|~np.isfinite(inv)]=np.nan
        rrai_name='RRAI_U_Uranium_Enrichment_Index'
        rrai_path=os.path.join(comb_dir,f'{rrai_name}.tif')
        write_raster(rrai_path,rrai,profile,gdal.GDT_Float32)
        if load_outputs: load_layer(rrai_path,rrai_name)
        if extract_idx in (0, 3, 4):
            self._classify_and_polygonise(rrai_name,rrai,profile,comb_dir,final_dir,logs,high_q,very_q,min_area,dict(settings,Index='RRAI-U',Combination_mode=r_mode,Weight_U_Th_norm=rw1,Weight_U_K_norm=rw2,Weight_one_minus_norm_K_Th_norm=rw3,Formula='Combination of (U/Th)norm, (U/K)norm and [1 - (K/Th)norm'),load_outputs=load_outputs)

        # B. Hybrid alteration/source-side index where K/U is available.
        if n_ku is not None:
            hrai=self._combine(n_uth,n_ku,inv,hw1,hw2,hw3,h_mode)
            hrai[~np.isfinite(n_uth)|~np.isfinite(n_ku)|~np.isfinite(inv)]=np.nan
            hrai_name='HRAI_KU_Hybrid_Alteration_Source_Index'
            hrai_path=os.path.join(comb_dir,f'{hrai_name}.tif')
            write_raster(hrai_path,hrai,profile,gdal.GDT_Float32)
            if load_outputs: load_layer(hrai_path,hrai_name)
            if extract_idx in (1, 3, 4):
                self._classify_and_polygonise(hrai_name,hrai,profile,comb_dir,final_dir,logs,high_q,very_q,min_area,dict(settings,Index='HRAI-KU',Combination_mode=h_mode,Weight_U_Th_norm=hw1,Weight_K_U_norm=hw2,Weight_one_minus_norm_K_Th_norm=hw3,Formula='Combination of (U/Th)norm, (K/U)norm and [1 - (K/Th)norm'),load_outputs=load_outputs)
        else:
            if extract_idx == 1:
                raise QgsProcessingException('HRAI-KU extraction was selected, but no K/U raster was supplied.')
            feedback.pushInfo('No K/U raster supplied; HRAI-KU was not generated.')

        # C. Hydrocarbon Radiometric Index; does not require K/U.
        hri=self._hri(UTh,UK,KTh,hriw1,hriw2,hriw3)
        hri_name='HRI_Hydrocarbon_Radiometric_Index'
        hri_path=os.path.join(comb_dir,f'{hri_name}.tif')
        write_raster(hri_path,hri,profile,gdal.GDT_Float32)
        if load_outputs: load_layer(hri_path,hri_name)
        if extract_idx in (2, 4):
            self._classify_and_polygonise(hri_name,hri,profile,comb_dir,final_dir,logs,high_q,very_q,min_area,dict(settings,Index='HRI',Weight_zln_U_Th=hriw1,Weight_zln_U_K=hriw2,Weight_penalty_zln_Th_K=hriw3,Formula='w1*z(ln(U/Th)) + w2*z(ln(U/K)) - w3*z(ln(Th/K))'),load_outputs=load_outputs)
        feedback.setProgress(100)
        feedback.pushInfo('Combined analysis and anomaly extraction complete.')
        return {self.OUTPUT_FOLDER:out}
