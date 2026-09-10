import com.ptc.pfc.pfcGlobal.pfcGlobal;
import com.ptc.pfc.pfcSession.Session;
import com.ptc.pfc.pfcModel.*;
import com.ptc.pfc.pfcModel2D.Model2D;
import com.ptc.pfc.pfcDrawing.Drawing;
import com.ptc.pfc.pfcWindow.Window;
import com.ptc.pfc.pfcCommand.DefaultUICommandActionListener;
import com.ptc.pfc.pfcCommand.UICommand;
import com.ptc.pfc.pfcExport.*;
import com.ptc.pfc.pfcArgument.pfcArgument;
import com.ptc.pfc.pfcFeature.FeatureType;
import com.ptc.pfc.pfcFeature.Features;
import com.ptc.pfc.pfcSolid.Solid;
import com.ptc.pfc.pfcComponentFeat.ComponentFeat;

import java.io.File;
import java.io.FileWriter;
import java.util.HashSet;
import java.util.Set;
import java.util.Vector;

/**
 * Creo J-Link Ribbon Commands
 *
 * Button 1: active model info
 * Button 2: all session drawings -> PDF + related CAD -> STEP
 * Button 3: Backup ACTIVE model + related files only
 *
 * BACKUP RULES
 * ------------
 * Scope:
 *   - Active PART     -> that part (FT instance -> generic once)
 *   - Active ASSEMBLY -> asm + recursive components (FT generic once each)
 *   - Active DRAWING  -> related solids only
 *
 * Conflict / skip / save:
 *   - folder newest > opened version -> CONFLICT (cancel remaining)
 *   - model already written earlier in THIS run -> SKIP
 *     (e.g. asm Backup also wrote a dependent part)
 *   - old file already in folder does NOT skip -> Backup auto-increment (max+1)
 *
 * Never Backup() a family-table instance — only its generic, once.
 */
public class CreoRibbonButton {

    private static final String OUT_DIR = "D:/export/creo_out";
    private static final String VERSION_DIR = "D:/export/creo_versions";

    private static UICommand cmdGetPart;
    private static UICommand cmdExport;
    private static UICommand cmdSaveVer;

    public static void start() {
        try {
            Session session = pfcGlobal.GetProESession();

            cmdGetPart = session.UICreateCommand(
                    "GetActivePartCmd", new GetPartNameListener());
            cmdGetPart.Designate(
                    "creoribbonbutton.txt",
                    "GetActivePartCmd",
                    "GetActivePartCmdHelp",
                    "GetActivePartCmd");

            cmdExport = session.UICreateCommand(
                    "ExportDrawingsPdfStepCmd",
                    new ExportDrawingsAndCadAction());
            cmdExport.Designate(
                    "creoribbonbutton.txt",
                    "ExportDrawingsPdfStepCmd",
                    "ExportDrawingsPdfStepCmdHelp",
                    "ExportDrawingsPdfStepCmd");

            cmdSaveVer = session.UICreateCommand(
                    "SaveOpenedVersionCmd",
                    new SaveOpenedVersionAction());
            cmdSaveVer.Designate(
                    "creoribbonbutton.txt",
                    "SaveOpenedVersionCmd",
                    "SaveOpenedVersionCmdHelp",
                    "SaveOpenedVersionCmd");
            UICommand cmdListMod = session.UICreateCommand(
                    "ListModifiedModelsCmd",
                    new ListModifiedModelsAction());
            cmdListMod.Designate(
                    "creoribbonbutton.txt",
                    "ListModifiedModelsCmd",
                    "ListModifiedModelsCmdHelp",
                    "ListModifiedModelsCmd");

        } catch (Exception e) {
            e.printStackTrace();
            System.out.println("J-Link Start Error: " + safeMessage(e));
        }
    }

    public static void stop() {
    }

    // =========================================================================
    // BUTTON 1
    // =========================================================================

    private static class GetPartNameListener
            extends DefaultUICommandActionListener {

        public void OnCommand() {
            try {
                Session session = pfcGlobal.GetProESession();
                Model active = session.GetCurrentModel();
                String message;

                if (active == null) {
                    message = "No active model open.";
                } else {
                    ModelDescriptor d = active.GetDescr();
                    String generic = d.GetGenericName();
                    boolean modified = active.GetIsModified();
                    if (generic == null) {
                        generic = "";
                    }
                    message = "Active model: " + active.GetFileName()
                            + "\nType: " + active.GetType()
                            + "\nInstance: " + d.GetInstanceName()
                            + "\nGeneric: " + generic
                            + "\nVersion: ." + d.GetFileVersion()
                            + "\nModified: " + modified;
                }
                session.UIShowMessageDialog(message, null);
            } catch (Exception e) {
                e.printStackTrace();
            }
        }
    }

    // =========================================================================
    // BUTTON 2
    // =========================================================================

    private static class ExportDrawingsAndCadAction
            extends DefaultUICommandActionListener {

        public void OnCommand() {
            try {
                Session session = pfcGlobal.GetProESession();
                new File(OUT_DIR).mkdirs();

                Models drawings =
                        session.ListModelsByType(ModelType.MDL_DRAWING);
                if (drawings == null || drawings.getarraysize() == 0) {
                    session.UIShowMessageDialog(
                            "No drawings in session.", null);
                    return;
                }

                Set exportedCadKeys = new HashSet();
                StringBuilder report = new StringBuilder();
                int pdfOk = 0;
                int stepOk = 0;
                int fail = 0;

                for (int i = 0; i < drawings.getarraysize(); i++) {
                    Drawing drw = (Drawing) drawings.get(i);

                    try {
                        exportDrawingPdf(session, drw);
                        pdfOk++;
                        report.append("PDF OK: ")
                                .append(drw.GetFileName()).append("\n");
                    } catch (Exception e) {
                        fail++;
                        report.append("PDF FAIL: ")
                                .append(drw.GetFileName())
                                .append(" -> ")
                                .append(safeMessage(e)).append("\n");
                    }

                    try {
                        Models solids = ((Model2D) drw).ListModels();
                        if (solids == null || solids.getarraysize() == 0) {
                            Model cur = ((Model2D) drw).GetCurrentSolid();
                            if (cur != null) {
                                solids = Models.create();
                                solids.append(cur);
                            }
                        }
                        if (solids == null) {
                            continue;
                        }

                        for (int s = 0; s < solids.getarraysize(); s++) {
                            Model related = solids.get(s);
                            Model cad = openCorrectInstance(session, related);
                            String key = cadKey(cad);
                            if (exportedCadKeys.contains(key)) {
                                continue;
                            }
                            exportedCadKeys.add(key);
                            exportCadStep(session, cad);
                            stepOk++;
                            report.append("STEP OK: ")
                                    .append(displayName(cad)).append("\n");
                        }
                    } catch (Exception e) {
                        fail++;
                        report.append("STEP FAIL for drawing ")
                                .append(drw.GetFileName())
                                .append(" -> ")
                                .append(safeMessage(e)).append("\n");
                    }
                }

                session.UIShowMessageDialog(
                        "Done.\nPDF: " + pdfOk
                                + "\nSTEP: " + stepOk
                                + "\nFails: " + fail
                                + "\nOut: " + OUT_DIR
                                + "\n\n" + report.toString(),
                        null);

            } catch (Exception e) {
                e.printStackTrace();
                try {
                    pfcGlobal.GetProESession().UIShowMessageDialog(
                            "Export error: " + safeMessage(e), null);
                } catch (Exception ignore) {
                }
            }
        }
    }

    private static void exportDrawingPdf(Session session, Drawing drw)
            throws Exception {
        Window w = session.GetModelWindow(drw);
        if (w == null) {
            w = session.CreateModelWindow(drw);
        }
        drw.Display();
        w.Activate();

        String base = stripExt(drw.GetInstanceName());
        String pdfPath = joinPath(OUT_DIR, base + ".pdf");

        PDFExportInstructions pdf =
                pfcExport.PDFExportInstructions_Create();
        pdf.SetFilePath(pdfPath);

        PDFOptions opts = PDFOptions.create();

        PDFOption color = pfcExport.PDFOption_Create();
        color.SetOptionType(PDFOptionType.PDFOPT_COLOR_DEPTH);
        color.SetOptionValue(
                pfcArgument.CreateIntArgValue(PDFColorDepth._PDF_CD_MONO));
        opts.append(color);

        PDFOption fonts = pfcExport.PDFOption_Create();
        fonts.SetOptionType(PDFOptionType.PDFOPT_FONT_STROKE);
        fonts.SetOptionValue(
                pfcArgument.CreateIntArgValue(
                        PDFFontStrokeMode._PDF_STROKE_ALL_FONTS));
        opts.append(fonts);

        PDFOption searchable = pfcExport.PDFOption_Create();
        searchable.SetOptionType(PDFOptionType.PDFOPT_SEARCHABLE_TEXT);
        searchable.SetOptionValue(pfcArgument.CreateBoolArgValue(true));
        opts.append(searchable);

        PDFOption noViewer = pfcExport.PDFOption_Create();
        noViewer.SetOptionType(PDFOptionType.PDFOPT_LAUNCH_VIEWER);
        noViewer.SetOptionValue(pfcArgument.CreateBoolArgValue(false));
        opts.append(noViewer);

        pdf.SetOptions(opts);
        drw.Export(pdfPath, pdf);
    }

    private static Model openCorrectInstance(Session session, Model related)
            throws Exception {
        ModelDescriptor desc = related.GetDescr();
        String instance = desc.GetInstanceName();
        String generic = desc.GetGenericName();

        if (generic == null || generic.trim().length() == 0) {
            return related;
        }

        Model inSession = session.GetModelFromDescr(desc);
        if (inSession != null) {
            return inSession;
        }

        ModelDescriptor instDesc = pfcModel.ModelDescriptor_Create(
                related.GetType(), instance, generic);
        try {
            String path = desc.GetPath();
            if (path != null && path.trim().length() > 0) {
                instDesc.SetPath(path);
            }
        } catch (Exception ignore) {
        }

        Model opened = session.RetrieveModel(instDesc);
        if (opened == null) {
            throw new RuntimeException(
                    "Could not open instance '" + instance
                            + "' of generic '" + generic + "'");
        }
        return opened;
    }

    private static void exportCadStep(Session session, Model cad)
            throws Exception {
        Window w = session.GetModelWindow(cad);
        if (w == null) {
            w = session.CreateModelWindow(cad);
        }
        cad.Display();
        w.Activate();

        GeometryFlags geom = pfcExport.GeometryFlags_Create();
        geom.SetAsSolids(true);

        STEP3DExportInstructions step =
                pfcExport.STEP3DExportInstructions_Create(
                        AssemblyConfiguration.EXPORT_ASM_SINGLE_FILE, geom);

        String base = stripExt(cad.GetInstanceName());
        base = base.replace('<', '_').replace('>', '_');
        cad.Export(joinPath(OUT_DIR, base + ".stp"), step);
    }

    private static String cadKey(Model m) throws Exception {
        ModelDescriptor d = m.GetDescr();
        String g = d.GetGenericName();
        if (g == null) {
            g = "";
        }
        return m.GetType().getValue() + "|" + d.GetInstanceName() + "|" + g;
    }

    private static String displayName(Model m) throws Exception {
        ModelDescriptor d = m.GetDescr();
        String g = d.GetGenericName();
        if (g != null && g.trim().length() > 0) {
            return d.GetInstanceName() + " <" + g + ">";
        }
        return d.GetInstanceName();
    }

    // =========================================================================
    // BUTTON 3 — ACTIVE + RELATED
    // =========================================================================

    private static class SaveOpenedVersionAction
            extends DefaultUICommandActionListener {

        public void OnCommand() {
            Session session = null;
            try {
                session = pfcGlobal.GetProESession();

                File versionDir = new File(VERSION_DIR);
                if (!versionDir.exists() && !versionDir.mkdirs()) {
                    throw new RuntimeException(
                            "Cannot create backup directory:\n" + VERSION_DIR);
                }

                Model active = session.GetCurrentModel();
                if (active == null) {
                    session.UIShowMessageDialog(
                            "No active model open.", null);
                    return;
                }

                Vector modelsToConsider = new Vector();
                collectRelatedModels(
                        session, active, modelsToConsider, new HashSet());

                Vector opened = new Vector();
                Set processedKeys = new HashSet();

                for (int i = 0; i < modelsToConsider.size(); i++) {
                    Model m = (Model) modelsToConsider.elementAt(i);
                    if (!isBackupCandidate(m)) {
                        continue;
                    }
                    VersionInfo info = createVersionInfo(session, m);
                    String key = backupModelKey(info);
                    if (processedKeys.contains(key)) {
                        continue;
                    }
                    processedKeys.add(key);
                    opened.addElement(info);
                }

                if (opened.size() == 0) {
                    session.UIShowMessageDialog(
                            "No PART/ASSEMBLY related to the active model.",
                            null);
                    return;
                }

                // Preflight conflicts only (folder newer than opened)
                int conflictCount = performPreflight(opened);
                if (conflictCount > 0) {
                    StringBuilder report = new StringBuilder();
                    for (int i = 0; i < opened.size(); i++) {
                        VersionInfo info = (VersionInfo) opened.elementAt(i);
                        if (!info.conflict) {
                            continue;
                        }
                        report.append("CONFLICT\n")
                                .append(info.description).append("\n");
                        for (int r = 0; r < info.resources.size(); r++) {
                            BackupResource resource =
                                    (BackupResource) info.resources
                                            .elementAt(r);
                            if (!resource.conflict) {
                                continue;
                            }
                            report.append("  ")
                                    .append(resource.baseName).append(".")
                                    .append(resource.ext).append("\n")
                                    .append("  Source: .")
                                    .append(resource.sourceVersion)
                                    .append("\n")
                                    .append("  Directory: .")
                                    .append(resource.diskVersion)
                                    .append("\n");
                        }
                        report.append("\n");
                        appendConflictManifest(info);
                    }

                    session.UIShowMessageDialog(
                            "BACKUP CANCELLED\n\n"
                                    + "A newer version exists in the "
                                    + "backup directory.\n\n"
                                    + "Conflicts: " + conflictCount
                                    + "\n\n" + report.toString()
                                    + "NO BACKUP WAS PERFORMED.\n\n"
                                    + "Directory:\n" + VERSION_DIR,
                            null);
                    return;
                }

                // Snapshot folder versions BEFORE this run's backups
                snapshotFolderVersions(opened);
                // Assemblies FIRST, parts LAST
                // (asm Backup often writes dependent parts into VERSION_DIR)
                Vector ordered = new Vector();
                for (int i = 0; i < opened.size(); i++) {
                    VersionInfo info = (VersionInfo) opened.elementAt(i);
                    if ("asm".equals(info.ext)) {
                        ordered.addElement(info);
                    }
                }
                for (int i = 0; i < opened.size(); i++) {
                    VersionInfo info = (VersionInfo) opened.elementAt(i);
                    if (!"asm".equals(info.ext)) {
                        ordered.addElement(info);
                    }
                }
                Set savedThisRun = new HashSet();
                StringBuilder report = new StringBuilder();
                int saved = 0;
                int fails = 0;
                int skipped = 0;
                boolean stoppedByConflict = false;
                for (int i = 0; i < ordered.size(); i++) {
                    VersionInfo info = (VersionInfo) ordered.elementAt(i);
                    String key = backupModelKey(info);
                    // Written earlier this run (explicitly or as dependent of asm Backup)
                    if (savedThisRun.contains(key) || appearedInFolderSince(info)) {
                        skipped++;
                        savedThisRun.add(key);
                        BackupResource res =
                                (BackupResource) info.resources.elementAt(0);
                        int now = findNewestVersionInDir(
                                VERSION_DIR, res.baseName, res.ext);
                        res.diskVersion = now;
                        res.fileName = res.baseName + "." + res.ext + "." + now;
                        report.append("SKIP (already saved this run): ")
                                .append(info.description)
                                .append(" -> ")
                                .append(res.fileName)
                                .append("\n");
                        appendManifestLine("SKIP_ALREADY_SAVED_THIS_RUN", info, "");
                        continue;
                    }
                    if (hasNewerVersionNow(info)) {
                        stoppedByConflict = true;
                        info.conflict = true;
                        report.append("CONFLICT DETECTED\n")
                                .append(info.description).append("\n");
                        appendConflictManifest(info);
                        break;
                    }
                    try {
                        backupModel(info);
                        savedThisRun.add(key);
                        // Mark dependents Creo just wrote with this Backup
                        markDependentsSavedThisRun(ordered, savedThisRun, i);
                        saved++;
                        BackupResource res =
                                (BackupResource) info.resources.elementAt(0);
                        report.append("SAVED: ")
                                .append(info.description)
                                .append(" -> ")
                                .append(res.fileName)
                                .append("\n");
                        appendSavedManifest(info);
                    } catch (Exception e) {
                        fails++;
                        report.append("FAIL: ")
                                .append(info.description)
                                .append("\nReason: ")
                                .append(safeMessage(e))
                                .append("\n\n");
                        appendFailedManifest(info, e);
                    }
                }

                StringBuilder finalMessage = new StringBuilder();
                if (stoppedByConflict) {
                    finalMessage.append("BACKUP PAUSED - CONFLICT\n\n");
                } else {
                    finalMessage.append("BACKUP COMPLETED\n\n");
                }
                finalMessage.append("Active: ")
                        .append(active.GetFileName()).append("\n")
                        .append("Unique models: ")
                        .append(ordered.size()).append("\n")
                        .append("Saved: ").append(saved).append("\n")
                        .append("Skipped (this run): ")
                        .append(skipped).append("\n")
                        .append("Fails: ").append(fails).append("\n\n")
                        .append("Directory:\n").append(VERSION_DIR)
                        .append("\n\n").append(report.toString());

                session.UIShowMessageDialog(finalMessage.toString(), null);

            } catch (Exception e) {
                e.printStackTrace();
                try {
                    if (session != null) {
                        session.UIShowMessageDialog(
                                "Save version error:\n\n" + safeMessage(e),
                                null);
                    }
                } catch (Exception ignore) {
                }
            }
        }
    }

    private static class ListModifiedModelsAction
            extends DefaultUICommandActionListener {

        public void OnCommand() {
            try {
                Session session = pfcGlobal.GetProESession();
                Models models = session.ListModels();

                if (models == null || models.getarraysize() == 0) {
                    session.UIShowMessageDialog("No models in session.", null);
                    return;
                }

                StringBuilder report = new StringBuilder();
                int count = 0;

                for (int i = 0; i < models.getarraysize(); i++) {
                    Model m = models.get(i);
                    try {
                        if (!m.GetIsModified()) {
                            continue;
                        }
                        count++;
                        ModelDescriptor d = m.GetDescr();
                        String generic = d.GetGenericName();
                        if (generic == null) {
                            generic = "";
                        }
                        report.append(count)
                                .append(") ")
                                .append(m.GetFileName())
                                .append("  | type=")
                                .append(m.GetType())
                                .append("  | instance=")
                                .append(d.GetInstanceName())
                                .append("  | generic=")
                                .append(generic)
                                .append("  | ver=.")
                                .append(d.GetFileVersion())
                                .append("\n");
                    } catch (Exception e) {
                        report.append("? ")
                                .append(m.GetFileName())
                                .append(" -> ")
                                .append(safeMessage(e))
                                .append("\n");
                    }
                }

                if (count == 0) {
                    session.UIShowMessageDialog(
                            "No modified models in session.", null);
                } else {
                    session.UIShowMessageDialog(
                            "Modified models: " + count
                                    + "\n\n" + report.toString(),
                            null);
                }
            } catch (Exception e) {
                e.printStackTrace();
                try {
                    pfcGlobal.GetProESession().UIShowMessageDialog(
                            "List modified error: " + safeMessage(e), null);
                } catch (Exception ignore) {
                }
            }
        }
    }

    // -------------------------------------------------------------------------
    // Collect related models
    // -------------------------------------------------------------------------

    private static void collectRelatedModels(
            Session session,
            Model root,
            Vector out,
            Set visitedKeys) throws Exception {

        if (root == null) {
            return;
        }

        ModelType type = root.GetType();

        if (type == ModelType.MDL_DRAWING) {
            Models solids = ((Model2D) root).ListModels();
            if (solids == null || solids.getarraysize() == 0) {
                Model cur = ((Model2D) root).GetCurrentSolid();
                if (cur != null) {
                    addUniqueModel(out, visitedKeys, cur);
                    if (cur.GetType() == ModelType.MDL_ASSEMBLY) {
                        collectAssemblyComponents(
                                session, (Solid) cur, out, visitedKeys);
                    }
                }
                return;
            }
            for (int i = 0; i < solids.getarraysize(); i++) {
                Model s = solids.get(i);
                addUniqueModel(out, visitedKeys, s);
                if (s != null && s.GetType() == ModelType.MDL_ASSEMBLY) {
                    collectAssemblyComponents(
                            session, (Solid) s, out, visitedKeys);
                }
            }
            return;
        }

        if (type == ModelType.MDL_PART) {
            addUniqueModel(out, visitedKeys, root);
            return;
        }

        if (type == ModelType.MDL_ASSEMBLY) {
            addUniqueModel(out, visitedKeys, root);
            collectAssemblyComponents(
                    session, (Solid) root, out, visitedKeys);
        }
    }

    private static void collectAssemblyComponents(
            Session session,
            Solid asm,
            Vector out,
            Set visitedKeys) throws Exception {

        Features components = asm.ListFeaturesByType(
                null, FeatureType.FEATTYPE_COMPONENT);
        if (components == null) {
            return;
        }

        for (int i = 0; i < components.getarraysize(); i++) {
            ComponentFeat component = (ComponentFeat) components.get(i);
            ModelDescriptor md = component.GetModelDescr();
            if (md == null) {
                continue;
            }

            Model componentModel = session.GetModelFromDescr(md);
            if (componentModel == null) {
                try {
                    componentModel = session.RetrieveModel(md);
                } catch (Exception ignore) {
                    componentModel = null;
                }
            }
            if (componentModel == null) {
                continue;
            }

            addUniqueModel(out, visitedKeys, componentModel);

            if (componentModel.GetType() == ModelType.MDL_ASSEMBLY) {
                collectAssemblyComponents(
                        session, (Solid) componentModel, out, visitedKeys);
            }
        }
    }

    private static void addUniqueModel(Vector out, Set visitedKeys, Model m)
            throws Exception {
        if (m == null) {
            return;
        }
        String key = cadKey(m).toLowerCase();
        if (visitedKeys.contains(key)) {
            return;
        }
        visitedKeys.add(key);
        out.addElement(m);
    }

    // -------------------------------------------------------------------------
    // Version / resource structs
    // -------------------------------------------------------------------------

    private static class VersionInfo {
        Model model;
        String instanceName;
        String genericName;
        String ext;
        int version;
        String baseName;
        String description;
        boolean familyTable;
        boolean conflict;
        Vector resources = new Vector();
    }

    private static class BackupResource {
        String baseName;
        String ext;
        String fileName;
        int sourceVersion;
        int diskVersion;
        int diskVersionBeforeRun;
        boolean conflict;
    }

    private static boolean isBackupCandidate(Model m) throws Exception {
        ModelType type = m.GetType();
        return type == ModelType.MDL_PART || type == ModelType.MDL_ASSEMBLY;
    }

    private static VersionInfo createVersionInfo(Session session, Model m)
            throws Exception {

        ModelDescriptor originalDesc = m.GetDescr();
        String originalInstance = originalDesc.GetInstanceName();
        String generic = originalDesc.GetGenericName();
        if (generic == null) {
            generic = "";
        }
        generic = generic.trim();

        if (generic.length() > 0) {
            Model genericModel = getGenericModel(session, m, generic);
            if (genericModel == null) {
                throw new RuntimeException(
                        "Cannot get generic '" + generic
                                + "' for instance '" + originalInstance + "'");
            }

            ModelDescriptor genericDesc = genericModel.GetDescr();
            VersionInfo info = new VersionInfo();
            info.model = genericModel;
            info.instanceName = genericDesc.GetInstanceName();
            info.genericName = "";
            info.version = genericDesc.GetFileVersion();
            info.familyTable = true;
            info.ext = extFor(genericModel.GetType());
            info.baseName = stripExt(info.instanceName).toLowerCase();
            info.description = "GENERIC " + info.instanceName
                    + " (from instance " + originalInstance + ")";

            BackupResource res = new BackupResource();
            res.baseName = info.baseName;
            res.ext = info.ext;
            res.sourceVersion = info.version;
            res.diskVersionBeforeRun = -1;
            res.fileName = res.baseName + "." + res.ext + "." + info.version;
            info.resources.addElement(res);
            return info;
        }

        VersionInfo info = new VersionInfo();
        info.model = m;
        info.instanceName = originalInstance;
        info.genericName = "";
        info.version = originalDesc.GetFileVersion();
        info.familyTable = false;
        info.ext = extFor(m.GetType());
        info.baseName = stripExt(info.instanceName).toLowerCase();
        info.description = info.instanceName;

        BackupResource res = new BackupResource();
        res.baseName = info.baseName;
        res.ext = info.ext;
        res.sourceVersion = info.version;
        res.diskVersionBeforeRun = -1;
        res.fileName = res.baseName + "." + res.ext + "." + info.version;
        info.resources.addElement(res);
        return info;
    }

    private static String extFor(ModelType type) throws Exception {
        if (type == ModelType.MDL_PART) {
            return "prt";
        }
        if (type == ModelType.MDL_ASSEMBLY) {
            return "asm";
        }
        throw new RuntimeException("Unsupported type: " + type);
    }

    private static Model getGenericModel(
            Session session, Model instanceModel, String genericName)
            throws Exception {

        Models models = session.ListModels();
        if (models != null) {
            for (int i = 0; i < models.getarraysize(); i++) {
                Model candidate = models.get(i);
                if (!isBackupCandidate(candidate)) {
                    continue;
                }
                ModelDescriptor cd = candidate.GetDescr();
                String candidateGeneric = cd.GetGenericName();
                String candidateInstance = cd.GetInstanceName();
                if ((candidateGeneric == null
                        || candidateGeneric.trim().length() == 0)
                        && candidateInstance != null
                        && candidateInstance.equalsIgnoreCase(genericName)) {
                    return candidate;
                }
            }
        }

        ModelDescriptor instanceDesc = instanceModel.GetDescr();
        ModelDescriptor genericDesc = pfcModel.ModelDescriptor_Create(
                instanceModel.GetType(), genericName, null);
        try {
            String path = instanceDesc.GetPath();
            if (path != null && path.trim().length() > 0) {
                genericDesc.SetPath(path);
            }
        } catch (Exception ignore) {
        }

        Model genericModel = session.GetModelFromDescr(genericDesc);
        if (genericModel != null) {
            return genericModel;
        }
        return session.RetrieveModel(genericDesc);
    }

    private static String backupModelKey(VersionInfo info) {
        String kind = info.familyTable ? "FAMILY" : "MODEL";
        return kind + "|"
                + info.baseName.toLowerCase() + "|"
                + info.ext.toLowerCase();
    }

    private static int performPreflight(Vector models) {
        int conflicts = 0;
        for (int i = 0; i < models.size(); i++) {
            VersionInfo info = (VersionInfo) models.elementAt(i);
            info.conflict = false;
            for (int r = 0; r < info.resources.size(); r++) {
                BackupResource resource =
                        (BackupResource) info.resources.elementAt(r);
                int newest = findNewestVersionInDir(
                        VERSION_DIR, resource.baseName, resource.ext);
                resource.diskVersion = newest;
                resource.conflict = (newest > resource.sourceVersion);
                if (resource.conflict) {
                    info.conflict = true;
                }
            }
            if (info.conflict) {
                conflicts++;
            }
        }
        return conflicts;
    }

    private static void snapshotFolderVersions(Vector opened) {
        for (int i = 0; i < opened.size(); i++) {
            VersionInfo info = (VersionInfo) opened.elementAt(i);
            BackupResource res =
                    (BackupResource) info.resources.elementAt(0);
            res.diskVersionBeforeRun = findNewestVersionInDir(
                    VERSION_DIR, res.baseName, res.ext);
        }
    }

    private static boolean appearedInFolderSince(VersionInfo info) {
        BackupResource res = (BackupResource) info.resources.elementAt(0);
        int now = findNewestVersionInDir(
                VERSION_DIR, res.baseName, res.ext);
        return now > res.diskVersionBeforeRun;
    }

    private static void markDependentsSavedThisRun(
            Vector ordered, Set savedThisRun, int currentIndex) {
        for (int j = currentIndex + 1; j < ordered.size(); j++) {
            VersionInfo other = (VersionInfo) ordered.elementAt(j);
            if (appearedInFolderSince(other)) {
                savedThisRun.add(backupModelKey(other));
            }
        }
    }

    private static boolean hasNewerVersionNow(VersionInfo info) {
        info.conflict = false;
        for (int r = 0; r < info.resources.size(); r++) {
            BackupResource resource =
                    (BackupResource) info.resources.elementAt(r);
            int newest = findNewestVersionInDir(
                    VERSION_DIR, resource.baseName, resource.ext);
            resource.diskVersion = newest;
            resource.conflict = (newest > resource.sourceVersion);
            if (resource.conflict) {
                info.conflict = true;
                return true;
            }
        }
        return false;
    }

    private static void backupModel(VersionInfo info) throws Exception {
        if (hasNewerVersionNow(info)) {
            throw new RuntimeException("Newer version detected before backup.");
        }
        BackupResource resource =
                (BackupResource) info.resources.elementAt(0);
        int before = findNewestVersionInDir(
                VERSION_DIR, resource.baseName, resource.ext);
        ModelDescriptor destination = pfcModel.ModelDescriptor_Create(
                info.model.GetType(),
                info.model.GetDescr().GetInstanceName(),
                null);
        destination.SetPath(VERSION_DIR);
        info.model.Backup(destination);
        int after = findNewestVersionInDir(
                VERSION_DIR, resource.baseName, resource.ext);
        if (after < 0 || after <= before) {
            throw new RuntimeException(
                    "Backup did not create a newer file for "
                            + resource.baseName + "." + resource.ext
                            + " in " + VERSION_DIR);
        }
        resource.diskVersion = after;
        resource.fileName =
                resource.baseName + "." + resource.ext + "." + after;
        // Clear Creo "modified" flag for THIS model only.
        // Backup to VERSION_DIR alone does not clear it.
        info.model.Save();
    }

    private static int findNewestVersionInDir(
            String dir, String baseName, String ext) {
        File folder = new File(dir);
        if (!folder.isDirectory()) {
            return -1;
        }
        File[] files = folder.listFiles();
        if (files == null) {
            return -1;
        }

        String prefix = (stripExt(baseName) + "." + ext + ".").toLowerCase();
        int max = -1;
        for (int i = 0; i < files.length; i++) {
            if (!files[i].isFile()) {
                continue;
            }
            String lower = files[i].getName().toLowerCase();
            if (!lower.startsWith(prefix)) {
                continue;
            }
            try {
                int version = Integer.parseInt(lower.substring(prefix.length()));
                if (version > max) {
                    max = version;
                }
            } catch (NumberFormatException ignore) {
            }
        }
        return max;
    }

    private static void appendSavedManifest(VersionInfo info) throws Exception {
        appendManifestLine("SAVED", info, "");
    }

    private static void appendConflictManifest(VersionInfo info)
            throws Exception {
        StringBuilder details = new StringBuilder();
        for (int r = 0; r < info.resources.size(); r++) {
            BackupResource resource =
                    (BackupResource) info.resources.elementAt(r);
            if (!resource.conflict) {
                continue;
            }
            details.append("file=").append(resource.baseName).append(".")
                    .append(resource.ext)
                    .append(" | source=").append(resource.sourceVersion)
                    .append(" | disk=").append(resource.diskVersion)
                    .append(" ; ");
        }
        appendManifestLine("CONFLICT", info, details.toString());
    }

    private static void appendFailedManifest(VersionInfo info, Exception e)
            throws Exception {
        appendManifestLine("FAILED", info, "error=" + safeMessage(e));
    }

    private static void appendManifestLine(
            String status, VersionInfo info, String extra) throws Exception {
        File log = new File(joinPath(VERSION_DIR, "opened_versions.log"));
        FileWriter fw = new FileWriter(log, true);
        try {
            String savedAs = "";
            if (info.resources.size() > 0) {
                BackupResource res =
                        (BackupResource) info.resources.elementAt(0);
                savedAs = res.fileName;
            }
            fw.write(new java.util.Date().toString()
                    + " | status=" + status
                    + " | model=" + info.description
                    + " | familyTable=" + info.familyTable
                    + " | openedVersion=" + info.version
                    + " | savedAs=" + savedAs
                    + " | extra=" + extra
                    + System.getProperty("line.separator"));
        } finally {
            fw.close();
        }
    }

    private static String stripExt(String name) {
        if (name == null || name.length() == 0) {
            return "model";
        }
        int dot = name.lastIndexOf('.');
        return (dot > 0) ? name.substring(0, dot) : name;
    }

    private static String joinPath(String dir, String file) {
        if (dir.endsWith("/") || dir.endsWith("\\")) {
            return dir + file;
        }
        return dir + "/" + file;
    }

    private static String safeMessage(Exception e) {
        if (e == null) {
            return "Unknown error";
        }
        String msg = e.getMessage();
        if (msg == null || msg.trim().length() == 0) {
            return e.toString();
        }
        return msg;
    }
}