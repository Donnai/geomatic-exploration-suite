# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication
from qgis.core import (
    QgsProcessingAlgorithm, QgsProcessingParameterRasterLayer,
    QgsProcessingParameterFolderDestination, QgsProcessingParameterNumber,
    QgsProcessingParameterBoolean, QgsProcessingParameterFile,
    QgsProcessingOutputFolder, QgsProcessingException
)
from osgeo import gdal
import os, csv, numpy as np
from .utils import read_raster, write_raster, array_stats, p_label, write_stats_csv, polygonize_class_raster, ensure_dir, load_layer

class RatioMapAnomalyExtraction(QgsProcessingAlgorithm):
    WORKFLOW_STEP_ID = 'step_03_ratio_anomaly_extraction'
    UTH='UTH_RATIO'; UK='UK_RATIO'; KU='KU_RATIO'; KTH='KTH_RATIO'; OUT='OUTPUT_FOLDER'; REVIEW='REVIEWED_THRESHOLDS_CSV'
    UH='UTH_HIGH_Q'; UV='UTH_VHIGH_Q'; KH='UK_HIGH_Q'; KV='UK_VHIGH_Q'; KUH='KU_HIGH_Q'; KUV='KU_VHIGH_Q'; KL='KTH_LOW_Q'; KVL='KTH_VLOW_Q'
    AREA='MIN_AREA_KM2'; LOAD='LOAD_OUTPUTS'; OUTPUT_FOLDER='OUTPUT_FOLDER_OUT'

    def tr(self,s): return QCoreApplication.translate('RatioMapAnomalyExtraction', s)
    def createInstance(self): return RatioMapAnomalyExtraction()
    def name(self): return '03_ratio_map_anomaly_extraction'
    def displayName(self): return self.tr('02 - Ratio Map Analysis and Anomaly Extraction')
    def group(self): return self.tr('Radiometric Analysis')
    def groupId(self): return 'radiometric_analysis'
    def shortHelpString(self):
        return self.tr('Classifies U/Th, U/K, K/U and K/Th ratio maps and polygonizes anomalies. Optionally reads the reviewed threshold CSV saved from Step 2b Histogram / Threshold Review.')

    def initAlgorithm(self, config=None):
        self.addParameter(QgsProcessingParameterRasterLayer(self.UTH,self.tr('U/Th ratio raster')))
        self.addParameter(QgsProcessingParameterRasterLayer(self.UK,self.tr('U/K ratio raster for RRAI-U')))
        self.addParameter(QgsProcessingParameterRasterLayer(self.KU,self.tr('K/U ratio raster for HRAI-KU')))
        self.addParameter(QgsProcessingParameterRasterLayer(self.KTH,self.tr('K/Th ratio raster')))
        self.addParameter(QgsProcessingParameterFolderDestination(self.OUT,self.tr('Output folder')))
        self.addParameter(QgsProcessingParameterFile(self.REVIEW,self.tr('Optional reviewed threshold CSV from Step 2b'),extension='csv',optional=True))
        for key,label,default in [(self.UH,'U/Th High percentile fallback',85.0),(self.UV,'U/Th Very High percentile fallback',95.0),(self.KH,'U/K High percentile fallback',85.0),(self.KV,'U/K Very High percentile fallback',95.0),(self.KUH,'K/U High percentile fallback',85.0),(self.KUV,'K/U Very High percentile fallback',95.0),(self.KL,'K/Th Low percentile fallback',15.0),(self.KVL,'K/Th Very Low percentile fallback',5.0)]:
            self.addParameter(QgsProcessingParameterNumber(key,self.tr(label),QgsProcessingParameterNumber.Double,defaultValue=default,minValue=0,maxValue=100))
        self.addParameter(QgsProcessingParameterNumber(self.AREA,self.tr('Minimum polygon area to retain, km²'),QgsProcessingParameterNumber.Double,defaultValue=0.0,minValue=0.0))
        self.addParameter(QgsProcessingParameterBoolean(self.LOAD,self.tr('Load anomaly polygons into QGIS'),defaultValue=True))
        self.addOutput(QgsProcessingOutputFolder(self.OUTPUT_FOLDER,self.tr('Output folder')))

    def _read_reviewed_thresholds(self, path):
        if not path or not os.path.exists(path):
            return {}
        required = ['U_Th_High','U_Th_VeryHigh','U_K_High','U_K_VeryHigh','K_U_High','K_U_VeryHigh','K_Th_Low','K_Th_VeryLow']
        out = {}
        with open(path, 'r', encoding='utf-8-sig', newline='') as f:
            rdr = csv.DictReader(f)
            if rdr.fieldnames and 'PARAMETER' in rdr.fieldnames and 'VALUE' in rdr.fieldnames:
                for row in rdr:
                    k = (row.get('PARAMETER') or '').strip()
                    if k in required:
                        try: out[k] = float(row.get('VALUE'))
                        except Exception: pass
        missing = [k for k in required if k not in out]
        if missing:
            raise QgsProcessingException('Reviewed threshold CSV is missing required fields: ' + ', '.join(missing))
        return out

    def processAlgorithm(self, parameters, context, feedback):
        uth_l=self.parameterAsRasterLayer(parameters,self.UTH,context); uk_l=self.parameterAsRasterLayer(parameters,self.UK,context); ku_l=self.parameterAsRasterLayer(parameters,self.KU,context); kth_l=self.parameterAsRasterLayer(parameters,self.KTH,context); out=self.parameterAsString(parameters,self.OUT,context)
        if not uth_l or not uk_l or not ku_l or not kth_l: raise QgsProcessingException('U/Th, U/K, K/U and K/Th ratio rasters are required.')
        cls_base=ensure_dir(os.path.join(out,'05_Ratio_Threshold_Class_Maps')); poly_base=ensure_dir(os.path.join(out,'06_Ratio_Map_Anomaly_Extraction')); logs=ensure_dir(os.path.join(out,'00_Stats_Logs'))
        UTh,profile=read_raster(uth_l.source()); UK,_=read_raster(uk_l.source()); KU,_=read_raster(ku_l.source()); KTh,_=read_raster(kth_l.source())
        if UTh.shape!=UK.shape or UTh.shape!=KU.shape or UTh.shape!=KTh.shape: raise QgsProcessingException('Input ratio rasters must have the same dimensions.')

        qs={self.UH:self.parameterAsDouble(parameters,self.UH,context),self.UV:self.parameterAsDouble(parameters,self.UV,context),self.KH:self.parameterAsDouble(parameters,self.KH,context),self.KV:self.parameterAsDouble(parameters,self.KV,context),self.KUH:self.parameterAsDouble(parameters,self.KUH,context),self.KUV:self.parameterAsDouble(parameters,self.KUV,context),self.KL:self.parameterAsDouble(parameters,self.KL,context),self.KVL:self.parameterAsDouble(parameters,self.KVL,context)}
        stats={'U_Th_ratio':array_stats(UTh,[qs[self.UH],qs[self.UV],5,10,15,50,85,90,95]),'U_K_ratio':array_stats(UK,[qs[self.KH],qs[self.KV],5,10,15,50,85,90,95]),'K_U_ratio':array_stats(KU,[qs[self.KUH],qs[self.KUV],5,10,15,50,85,90,95]),'K_Th_ratio':array_stats(KTh,[qs[self.KL],qs[self.KVL],5,10,15,50,85,90,95])}

        review_path = self.parameterAsFile(parameters, self.REVIEW, context)
        reviewed = self._read_reviewed_thresholds(review_path)
        if reviewed:
            feedback.pushInfo('Using reviewed threshold CSV: ' + review_path)
            uth_h=reviewed['U_Th_High']; uth_v=reviewed['U_Th_VeryHigh']; uk_h=reviewed['U_K_High']; uk_v=reviewed['U_K_VeryHigh']; ku_h=reviewed['K_U_High']; ku_v=reviewed['K_U_VeryHigh']; kth_lw=reviewed['K_Th_Low']; kth_vl=reviewed['K_Th_VeryLow']
            threshold_source = 'Reviewed threshold CSV'
        else:
            feedback.pushInfo('No reviewed threshold CSV supplied; using percentile fallback parameters.')
            uth_h=stats['U_Th_ratio'][f'P{p_label(qs[self.UH])}']; uth_v=stats['U_Th_ratio'][f'P{p_label(qs[self.UV])}']; uk_h=stats['U_K_ratio'][f'P{p_label(qs[self.KH])}']; uk_v=stats['U_K_ratio'][f'P{p_label(qs[self.KV])}']; ku_h=stats['K_U_ratio'][f'P{p_label(qs[self.KUH])}']; ku_v=stats['K_U_ratio'][f'P{p_label(qs[self.KUV])}']; kth_lw=stats['K_Th_ratio'][f'P{p_label(qs[self.KL])}']; kth_vl=stats['K_Th_ratio'][f'P{p_label(qs[self.KVL])}']
            threshold_source = 'Percentile fallback parameters'

        feedback.pushInfo('Classifying ratio anomaly rasters.')
        classes={}
        c=np.zeros(UTh.shape,dtype=np.uint8); c[np.isfinite(UTh)&(UTh>=uth_h)]=1; c[np.isfinite(UTh)&(UTh>=uth_v)]=2; classes['U_Th_anomaly_class']=c
        c=np.zeros(UK.shape,dtype=np.uint8); c[np.isfinite(UK)&(UK>=uk_h)]=1; c[np.isfinite(UK)&(UK>=uk_v)]=2; classes['U_K_anomaly_class']=c
        c=np.zeros(KU.shape,dtype=np.uint8); c[np.isfinite(KU)&(KU>=ku_h)]=1; c[np.isfinite(KU)&(KU>=ku_v)]=2; classes['K_U_anomaly_class']=c
        c=np.zeros(KTh.shape,dtype=np.uint8); c[np.isfinite(KTh)&(KTh<=kth_lw)]=1; c[np.isfinite(KTh)&(KTh<=kth_vl)]=2; classes['K_Th_anomaly_class']=c
        gpkg=[]; area=self.parameterAsDouble(parameters,self.AREA,context)
        ratio_dirs = {
            'U_Th_anomaly_class': ('U_Th', 'U_Th_ratio_class'),
            'U_K_anomaly_class': ('U_K', 'U_K_ratio_class'),
            'K_U_anomaly_class': ('K_U', 'K_U_ratio_class'),
            'K_Th_anomaly_class': ('K_Th', 'K_Th_ratio_class'),
        }
        for name,arr in classes.items():
            ratio_dir, out_base = ratio_dirs.get(name, ('Other', name))
            cls_dir = ensure_dir(os.path.join(cls_base, ratio_dir))
            poly_dir = ensure_dir(os.path.join(poly_base, ratio_dir))
            rp=os.path.join(cls_dir,f'{out_base}.tif'); write_raster(rp,arr,profile,gdal.GDT_Byte,nodata=0)
            gp=os.path.join(poly_dir,f'{out_base}_polygons.gpkg'); polygonize_class_raster(rp,gp,out_base.replace('_class',''),[1,2],area); gpkg.append((gp,out_base.replace('_class','_polygons')))
        write_stats_csv(os.path.join(logs,'ratio_anomaly_extraction_thresholds.csv'),stats,{'Threshold_source':threshold_source,'Reviewed_threshold_CSV':review_path or '', 'U_Th_High':uth_h,'U_Th_VeryHigh':uth_v,'U_K_High':uk_h,'U_K_VeryHigh':uk_v,'K_U_High':ku_h,'K_U_VeryHigh':ku_v,'K_Th_Low':kth_lw,'K_Th_VeryLow':kth_vl,'Minimum_area_km2':area})
        if self.parameterAsBoolean(parameters,self.LOAD,context):
            for p,n in gpkg: load_layer(p,n)
        feedback.setProgress(100); feedback.pushInfo('Ratio anomaly extraction complete.')
        return {self.OUTPUT_FOLDER:out}
