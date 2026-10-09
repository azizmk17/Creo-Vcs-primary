import java.io.File;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import javax.swing.JOptionPane;

import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcBase.ActionListener;
import com.ptc.pfc.pfcCommand.DefaultUICommandActionListener;
import com.ptc.pfc.pfcCommand.UICommand;
import com.ptc.pfc.pfcCommand.UICommandBracketListener;
import com.ptc.pfc.pfcExceptions.XCancelProEAction;
import com.ptc.pfc.pfcGlobal.pfcGlobal;
import com.ptc.pfc.pfcModel.Dependencies;
import com.ptc.pfc.pfcModel.Dependency;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcModel.ModelDescriptor;
import com.ptc.pfc.pfcModel.pfcModel;
import com.ptc.pfc.pfcModel.Models;
import com.ptc.pfc.pfcModel2D.Model2D;
import com.ptc.pfc.pfcObject.Child;
import com.ptc.pfc.pfcObject.Parent;
import com.ptc.pfc.pfcSession.Session;
import com.ptc.pfc.pfcWindow.Window;
import com.ptc.pfc.pfcComponentFeat.ComponentFeat;
import com.ptc.pfc.pfcFeature.Feature;
import com.ptc.pfc.pfcFeature.Features;
import com.ptc.pfc.pfcFeature.FeatureStatus;
import com.ptc.pfc.pfcFeature.FeatureType;
import com.ptc.pfc.pfcSolid.Solid;

public class NexusJLink {
    private static Session session;
    private static NexusApiClient api;
    private static final List<CommandGuardRegistration> commandGuards =
        new ArrayList<CommandGuardRegistration>();
    private static final List<ActionListener> mutationGuards =
        new ArrayList<ActionListener>();
    private static final Set<String> registeredGuardNames =
        new LinkedHashSet<String>();
    private static final Set<String> localDraftModels =
        new LinkedHashSet<String>();
    private static final Map<String, Integer> modelRegenerationDepth =
        new LinkedHashMap<String, Integer>();
    private static final ThreadLocal<Set<String>> authorizedEditModels =
        new ThreadLocal<Set<String>>();
    private static Map<String, Object> selectedWorkspace;

    public static void start() {
        try {
            session = pfcGlobal.GetProESession();
            api = new NexusApiClient();
            installMutationGuards();
            installCommandGuards();
            installMenu();
            System.out.println("Nexus PDM J-Link started.");
        } catch (Throwable error) {
            showError(error);
        }
    }

    public static void stop() {
        for (CommandGuardRegistration registration : commandGuards) {
            try {
                registration.command.RemoveActionListener(registration.listener);
            } catch (Throwable ignored) {
            }
        }
        commandGuards.clear();
        for (ActionListener listener : mutationGuards) {
            try {
                session.RemoveActionListener(listener);
            } catch (Throwable ignored) {
            }
        }
        mutationGuards.clear();
        registeredGuardNames.clear();
        localDraftModels.clear();
        synchronized (modelRegenerationDepth) {
            modelRegenerationDepth.clear();
        }
        authorizedEditModels.remove();
        selectedWorkspace = null;
        api = null;
        session = null;
    }

    private static void installCommandGuards() throws jxthrowable {
        addCommandGuard("ProCmdModelSave", "save");
        addCommandGuard("ProCmdModelRename", "rename");
        String[] editCommands = new String[] {
            "ProCmdModelEdit",
            "ProCmdModelModify",
            "ProCmdFeatEdit",
            "ProCmdFeatEditDef",
            "ProCmdFeatRedefine",
            "ProCmdFeatDelete",
            "ProCmdEditDelete",
            "ProCmdFeatSuppress",
            "ProCmdFeatResume",
            "ProCmdDdim",
            "ProCmdEditDim",
            // Creo 3.0 commands captured from the actual ribbon/tree workflows.
            "ProCmdDynEdit",
            "ProCmdEditValueDim",
            "mod_dim_emb",
            "mod_partdim_emb",
            "ProCmdL05Edit",
            "ProCmdL05Edit@PopupMenuTree",
            "ProCmdL05EditFeat",
            "ProCmdEditNoAutoRegen@PopupMenuTree",
            "ProCmdRedefine",
            "ProCmdRedefine@PopupMenuTree",
            "ProCmdEditProperties",
            "ProCmdEditProperties@PopupMenuTree",
            "ProCmdEditOneByOne",
            "ProCmdFtExtrude",
            "ProCmdFtHole",
            "ProCmdFtRevolve",
            "ProCmdFtSweep",
            "ProCmdFtBlend",
            "ProCmdFtRound",
            "ProCmdFtChamfer",
            "ProCmdFtDraft",
            "ProCmdFtPattern",
            "ProCmdFtMirror",
            "ProCmdFtShell",
            "ProCmdFtRib",
            "ProCmdDatumAxis",
            "ProCmdDatumCsys",
            "ProCmdDatumPlane",
            "ProCmdDatumPointGeneral",
            "ProCmdDatumSketCurve",
            "ProCmdSketDimension",
            "ProCmdEditChainSpline",
            "ProCmdEditCorner",
            "ProCmdEditDelSeg",
            "ProCmdEditMirror",
            "ProCmdEditTransRotScale",
            "ProCmdCompAssem",
            "ProCmdCompEditPlacement",
            "ProCmdCompConstr",
            "ProCmdCompRedefine",
            "ProCmdCompPackage",
            "ProCmdCompReplace",
            "ProCmdCompDelete",
            "ProCmdSuppressFeat",
            "ProCmdSuppress@PopupMenuTree",
            "ProCmdResume@PopupMenuTree",
            "ProCmdDelete@PopupMenuTree",
            "ProCmdEditPaste",
            "ProCmdModelParams",
            "ProCmdParamEdit",
            "ProCmdRelations",
            // Drawing dimensions, notes, symbols, views, and sheet edits.
            "ProCmdDwgCrStdNewRefDim",
            "ProCmdDtlInsFreeNote",
            "ProCmdDwgCreate3DDatum",
            "ProCmdDwgCreateBallon",
            "ProCmdDwgCreateSurfFin",
            "ProCmdDwgCrGtolMakeTarg",
            "ProCmdDwgCrGtolSpecTol",
            "ProCmdDwgCrSnapLine",
            "ProCmdDwgCrSymInstCust",
            "ProCmdDwgDraftGroup",
            "ProCmdDwgViewAux",
            "ProCmdDwgViewDet",
            "ProCmdDwgViewGen",
            "ProCmdDwgViewProj",
            "ProCmdDwgCrStdDim",
            "ProCmdDwgCreateDim",
            "ProCmdDwgModDim",
            "ProCmdDwgMoveDim",
            "ProCmdDwgCreateNote",
            "ProCmdDwgEditNote",
            "ProCmdDwgModNote",
            "ProCmdDwgCreateSymbol",
            "ProCmdDwgEditSymbol",
            "ProCmdDwgCrGeneralView",
            "ProCmdDwgViewProperties",
            "ProCmdDwgMoveView",
            "ProCmdDwgDeleteView",
            "ProCmdDwgDelete",
            "ProCmdDwgEditValue",
            "ProCmdDwgParams",
            "ProCmdDwgFormat",
            "ProCmdDwgSheetSetup",
            "ProCmdCreateNote",
            "ProCmdEditNote",
            "ProCmdAnnotationEdit",
            "PH.L.PIM_Addpb.l0",
            "PH.pop_constr_offset",
            "psh_delete1"
        };
        for (String commandName : editCommands) {
            addEditCommandGuard(commandName);
        }
    }

    private static void installMutationGuards() throws jxthrowable {
        if (!mutationGuards.isEmpty()) return;
        ActionListener[] listeners = new ActionListener[] {
            new NexusModelMutationGuard(),
            new NexusFeatureMutationGuard(),
            new NexusSolidMutationGuard(),
            new NexusSessionMutationGuard()
        };
        for (int index = 0; index < listeners.length; index++) {
            try {
                session.AddActionListener(listeners[index]);
                mutationGuards.add(listeners[index]);
            } catch (Throwable error) {
                System.out.println(
                    "Nexus PDM mutation guard unavailable: "
                        + listeners[index].getClass().getName() + " - " + error
                );
            }
        }
        System.out.println(
            "Nexus PDM session-wide mutation guards registered: "
                + mutationGuards.size()
        );
    }

    static void refreshCommandProtection() {
        if (session == null || api == null) return;
        try {
            installCommandGuards();
        } catch (Throwable error) {
            System.out.println("Nexus PDM command protection refresh failed: " + error);
        }
    }

    private static void addCommandGuard(String commandName, String action) throws jxthrowable {
        if (registeredGuardNames.contains(commandName)) {
            return;
        }
        UICommand command = session.UIGetCommand(commandName);
        if (command == null) {
            System.out.println("Nexus PDM save guard unavailable for Creo command: " + commandName);
            return;
        }
        NexusSaveGuard listener = new NexusSaveGuard(session, api, action);
        command.AddActionListener(listener);
        commandGuards.add(new CommandGuardRegistration(command, listener));
        registeredGuardNames.add(commandName);
        System.out.println("Nexus PDM save guard registered: " + commandName);
    }

    private static void addEditCommandGuard(String commandName) throws jxthrowable {
        if (registeredGuardNames.contains(commandName)) {
            return;
        }
        UICommand command = session.UIGetCommand(commandName);
        if (command == null) {
            System.out.println("Nexus PDM edit guard unavailable for Creo command: " + commandName);
            return;
        }
        NexusEditGuard listener = new NexusEditGuard();
        command.AddActionListener(listener);
        commandGuards.add(new CommandGuardRegistration(command, listener));
        registeredGuardNames.add(commandName);
        System.out.println("Nexus PDM edit guard registered: " + commandName);
    }

    private static void installMenu() throws jxthrowable {
        session.UIAddMenu("NexusPDM", "Applications", "nexus_jlink.txt", null);
        addCommand("NexusPDM.Connect", "NexusConnect", "NexusConnectHelp", new CommandAction() {
            public void run() throws Exception { connect(); }
        });
        addCommand("NexusPDM.Workspace", "NexusWorkspace", "NexusWorkspaceHelp", new CommandAction() {
            public void run() throws Exception { chooseWorkspace(); }
        });
        addCommand("NexusPDM.Retrieve", "NexusRetrieve", "NexusRetrieveHelp", new CommandAction() {
            public void run() throws Exception { retrieve(); }
        });
        addCommand("NexusPDM.Status", "NexusStatus", "NexusStatusHelp", new CommandAction() {
            public void run() throws Exception { showStatus(); }
        });
        addCommand("NexusPDM.WorkspaceStatus", "NexusWorkspaceStatus", "NexusWorkspaceStatusHelp", new CommandAction() {
            public void run() throws Exception { showWorkspaceStatus(); }
        });
        addCommand("NexusPDM.History", "NexusHistory", "NexusHistoryHelp", new CommandAction() {
            public void run() throws Exception { showHistory(); }
        });
        addCommand("NexusPDM.Checkout", "NexusCheckout", "NexusCheckoutHelp", new CommandAction() {
            public void run() throws Exception { checkout(); }
        });
        addCommand("NexusPDM.Checkin", "NexusCheckin", "NexusCheckinHelp", new CommandAction() {
            public void run() throws Exception { checkin(); }
        });
        addCommand("NexusPDM.Undo", "NexusUndo", "NexusUndoHelp", new CommandAction() {
            public void run() throws Exception { undoCheckout(); }
        });
        addCommand("NexusPDM.Revise", "NexusRevise", "NexusReviseHelp", new CommandAction() {
            public void run() throws Exception { reviseCurrent(); }
        });
        addCommand("NexusPDM.Release", "NexusRelease", "NexusReleaseHelp", new CommandAction() {
            public void run() throws Exception { releaseCurrent(); }
        });
    }

    private static void addCommand(
        String commandName, String labelKey, String helpKey, CommandAction action
    ) throws jxthrowable {
        UICommand command = session.UICreateCommand(commandName, new CommandListener(action));
        String iconName = commandName.substring("NexusPDM.".length());
        command.SetIcon("nexus_" + iconName.toLowerCase() + ".png");
        command.Designate("nexus_jlink.txt", labelKey, helpKey, helpKey);
        session.UIAddButton(
            command, "NexusPDM", null, labelKey, helpKey, "nexus_jlink.txt"
        );
    }

    private static void connect() throws Exception {
        Map<String, Object> context = requireContext();
        installCommandGuards();
        Map<String, Object> user = MiniJson.object(context.get("user"));
        Map<String, Object> project = MiniJson.object(context.get("project"));
        String workspaceText = selectedWorkspace == null
            ? "Not selected"
            : MiniJson.text(selectedWorkspace, "name") + "\n" + MiniJson.text(selectedWorkspace, "path");
        NexusDialogs.info(
            "Connected to Nexus.\n\nUser: " + MiniJson.text(user, "username")
                + "\nProduct: " + projectLabel(project)
                + "\nWorkspace: " + workspaceText,
            "Nexus PDM"
        );
    }

    private static Map<String, Object> requireContext() throws Exception {
        Map<String, Object> context = api.getContext();
        if (!MiniJson.bool(context, "logged_in")) {
            throw new IllegalStateException("Sign in to Nexus before using the Creo integration.");
        }
        if (!(context.get("project") instanceof Map)) {
            throw new IllegalStateException("Select a product and version in Nexus first.");
        }
        return context;
    }

    static Map<String, Object> resolveCadForModel(Model model) throws Exception {
        if (api == null || model == null) {
            return new LinkedHashMap<String, Object>();
        }
        String workspaceId = selectedWorkspace == null
            ? ""
            : MiniJson.text(selectedWorkspace, "id");
        return api.resolveCad(logicalModelFileName(model), workspaceId);
    }

    private static String projectLabel(Map<String, Object> project) {
        String number = MiniJson.text(project, "product_number");
        String name = MiniJson.text(project, "name");
        String version = MiniJson.text(project, "version_label");
        String label = number.length() > 0 ? number + " - " + name : name;
        return version.length() > 0 ? label + " / " + version : label;
    }

    private static boolean isCadNameManaged(
        String fileName, Map<String, Object> workspace
    ) throws Exception {
        Map<String, Object> status = api.resolveCad(
            fileName, MiniJson.text(workspace, "id")
        );
        return MiniJson.bool(status, "managed");
    }

    private static boolean registerUnmanagedWorkspaceModel(
        Model current, Map<String, Object> workspace
    ) throws Exception {
        String currentName = logicalModelFileName(current);
        Map<String, Object> workspaceState = loadWorkspaceState(
            MiniJson.text(workspace, "id")
        );
        List<Object> localFiles = MiniJson.array(workspaceState.get("local_files"));
        Map<String, Object> source = findUnmappedWorkspaceFile(localFiles, currentName);
        if (source == null || !loadedModelMatchesWorkspaceFile(
            current, MiniJson.text(source, "path")
        )) {
            throw new IllegalStateException(
                "This CAD file is not an unmapped file in the selected Nexus workspace. "
                    + "Only files physically present in that workspace can be registered here."
            );
        }
        List<Map<String, Object>> files = new ArrayList<Map<String, Object>>();
        List<String> ownerNames = new ArrayList<String>();
        if (current instanceof Model2D) {
            Models references = ((Model2D) current).ListModels();
            if (references != null) {
                for (int index = 0; index < references.getarraysize(); index++) {
                    Model reference = references.get(index);
                    String name = logicalModelFileName(reference);
                    String lower = name.toLowerCase();
                    if ((lower.endsWith(".prt") || lower.endsWith(".asm"))
                        && !ownerNames.contains(name)) ownerNames.add(name);
                }
            }
            if (ownerNames.size() != 1) {
                throw new IllegalStateException(
                    "A new drawing must reference exactly one PRT or ASM model before it can be registered."
                );
            }
            String ownerName = ownerNames.get(0);
            if (!isCadNameManaged(ownerName, workspace)) {
                Map<String, Object> ownerSource = findUnmappedWorkspaceFile(
                    localFiles, ownerName
                );
                Model ownerModel = findSessionModel(ownerName);
                if (ownerSource == null || ownerModel == null
                    || !loadedModelMatchesWorkspaceFile(
                        ownerModel, MiniJson.text(ownerSource, "path")
                    )) {
                    throw new IllegalStateException(
                        "The drawing's unregistered model " + ownerName
                            + " must also be present and loaded from this Nexus workspace."
                    );
                }
                files.add(registrationFile(ownerSource));
            }
        }
        Map<String, Object> currentFile = registrationFile(source);
        if (!ownerNames.isEmpty()) currentFile.put("drawing_models", ownerNames);
        files.add(currentFile);
        StringBuilder prompt = new StringBuilder();
        prompt.append("Nexus will register and check out these new native CAD files in the selected workspace:\n\n");
        for (Map<String, Object> file : files) {
            prompt.append("  ").append(MiniJson.text(file, "filename")).append("\n");
        }
        prompt.append("\nContinue?");
        if (!NexusDialogs.confirm(
            prompt.toString(), "Register New CAD", JOptionPane.QUESTION_MESSAGE
        )) return false;
        api.registerNewCad(MiniJson.text(workspace, "id"), files);
        return true;
    }

    private static Map<String, Object> findUnmappedWorkspaceFile(
        List<Object> localFiles, String wantedName
    ) {
        for (Object value : localFiles) {
            Map<String, Object> local = MiniJson.object(value);
            if ("UNMAPPED".equalsIgnoreCase(MiniJson.text(local, "status"))
                && logicalCreoFileName(MiniJson.text(local, "logical_file_name"))
                    .equalsIgnoreCase(logicalCreoFileName(wantedName))) {
                return local;
            }
        }
        return null;
    }

    private static Map<String, Object> registrationFile(Map<String, Object> local) {
        Map<String, Object> file = new LinkedHashMap<String, Object>();
        file.put("filename", MiniJson.text(local, "filename"));
        file.put("path", MiniJson.text(local, "path"));
        return file;
    }

    private static Model findSessionModel(String wantedName) throws Exception {
        Models models = session.ListModels();
        if (models == null) return null;
        for (int index = 0; index < models.getarraysize(); index++) {
            Model model = models.get(index);
            if (model != null && logicalModelFileName(model)
                .equalsIgnoreCase(logicalCreoFileName(wantedName))) return model;
        }
        return null;
    }

    private static Map<String, Object> chooseWorkspace() throws Exception {
        requireContext();
        installCommandGuards();
        String previousWorkspaceId = selectedWorkspace == null
            ? ""
            : MiniJson.text(selectedWorkspace, "id");
        List<Object> raw = api.listWorkspaces();
        List<WorkspaceChoice> choices = new ArrayList<WorkspaceChoice>();
        for (Object item : raw) {
            choices.add(new WorkspaceChoice(MiniJson.object(item)));
        }
        choices.add(new WorkspaceChoice(null));
        Object selected = NexusDialogs.choose(
            "Select the local CAD workspace used by this Creo session:",
            "Nexus CAD Workspace",
            choices.toArray(),
            choices.get(0)
        );
        if (!(selected instanceof WorkspaceChoice)) {
            return selectedWorkspace;
        }
        WorkspaceChoice choice = (WorkspaceChoice) selected;
        if (choice.workspace == null) {
            String name = NexusDialogs.input(
                "Workspace name:", "Create Nexus CAD Workspace"
            );
            if (name == null || name.trim().length() == 0) {
                return selectedWorkspace;
            }
            selectedWorkspace = api.createWorkspace(name.trim());
        } else {
            selectedWorkspace = choice.workspace;
        }
        if (!previousWorkspaceId.equalsIgnoreCase(MiniJson.text(selectedWorkspace, "id"))) {
            localDraftModels.clear();
        }
        session.ChangeDirectory(MiniJson.text(selectedWorkspace, "path"));
        NexusDialogs.info(
            "Creo working directory:\n" + MiniJson.text(selectedWorkspace, "path"),
            "Nexus CAD Workspace"
        );
        return selectedWorkspace;
    }

    private static Map<String, Object> requireWorkspace() throws Exception {
        requireContext();
        if (selectedWorkspace == null) {
            chooseWorkspace();
        }
        if (selectedWorkspace == null) {
            throw new IllegalStateException("Select a Nexus CAD workspace first.");
        }
        return selectedWorkspace;
    }

    private static void retrieve() throws Exception {
        Map<String, Object> workspace = requireWorkspace();
        installCommandGuards();
        Map<String, Object> status = chooseProjectCadDocument("Retrieve from Nexus");
        if (status == null) {
            return;
        }
        requireManaged(status);
        int cadId = MiniJson.integer(status, "id");
        String workspaceId = MiniJson.text(workspace, "id");
        String action = MiniJson.text(status, "_nexus_retrieve_action");
        Map<String, Object> result;
        if ("DRAWING".equals(action)) {
            int drawingId = MiniJson.integer(status, "_nexus_selected_drawing_id");
            String modelName = MiniJson.text(status, "file_name");
            String drawingName = selectedDrawingName(status, drawingId);
            String modelKey = logicalCreoFileName(modelName).toLowerCase();
            LoadedModelInfo loaded = loadedCreoModels().get(modelKey);
            boolean modelReplaced = false;
            if (loaded != null) {
                String drawingAction = NexusDialogs.chooseRetrieveDrawingAction(
                    modelName, drawingName
                );
                if ("Cancel".equals(drawingAction)) return;
                if ("Replace Model + Drawing".equals(drawingAction)) {
                    ensureReloadIsSafe(loaded.model, workspace, false);
                    eraseForReload(loaded.model);
                    Map<String, Object> modelResult = api.retrieve(cadId, workspaceId);
                    displayManagedResult(modelResult);
                    modelReplaced = true;
                }
                result = api.retrieveDrawing(cadId, drawingId, workspaceId);
            } else {
                Map<String, Object> modelResult = api.retrieve(cadId, workspaceId);
                displayManagedResult(modelResult);
                result = api.retrieveDrawing(cadId, drawingId, workspaceId);
            }
            displayManagedResult(result);
            NexusDialogs.info(
                drawingName + " was retrieved. "
                    + (loaded == null ? "The model package was retrieved as well."
                        : modelReplaced ? "The selected model was replaced."
                            : "The selected model was kept loaded."),
                "Nexus Retrieve"
            );
            return;
        }
        result = api.retrieve(cadId, workspaceId);
        displayManagedResult(result);
        Map<String, Object> refreshed = MiniJson.object(result.get("cad"));
        String mode = MiniJson.bool(refreshed, "can_modify") ? "editable" : "read-only";
        NexusDialogs.info(
            MiniJson.text(refreshed, "file_name") + " was retrieved " + mode + ".",
            "Nexus Retrieve"
        );
    }

    private static String selectedDrawingName(
        Map<String, Object> cad, int drawingId
    ) {
        for (Object raw : MiniJson.array(cad.get("related_drawings"))) {
            Map<String, Object> drawing = MiniJson.object(raw);
            if (MiniJson.integer(drawing, "id") == drawingId) {
                String name = MiniJson.text(drawing, "file_name");
                return name.length() == 0 ? MiniJson.text(drawing, "name") : name;
            }
        }
        return "Related drawing";
    }

    private static Map<String, Object> chooseProjectCadDocument(String title) throws Exception {
        List<Object> raw;
        try {
            raw = api.listCadDocuments();
        } catch (NexusApiException error) {
            if ("route_not_found".equals(error.getCode())) {
                throw new IllegalStateException(
                    "The running Nexus bridge does not include the CAD Document list route yet.\n\n"
                        + "Close and restart the Nexus desktop app, then reconnect Creo to the project."
                );
            }
            throw error;
        }
        List<Map<String, Object>> choices = new ArrayList<Map<String, Object>>();
        for (Object item : raw) {
            Map<String, Object> cad = MiniJson.object(item);
            if (MiniJson.bool(cad, "managed")
                && !"DRAWING".equalsIgnoreCase(MiniJson.text(cad, "category"))) {
                choices.add(cad);
            }
        }
        if (choices.isEmpty()) {
            throw new IllegalStateException(
                "The active Nexus project has no managed part or assembly CAD Documents to retrieve."
            );
        }
        return NexusDialogs.chooseCadDocument(
            title,
            "Select a CAD Document from the active Nexus project:",
            choices
        );
    }

    private static void showStatus() throws Exception {
        Map<String, Object> status = resolveCurrentModel();
        String owner = MiniJson.text(status, "checked_out_by_username");
        String ownerLine = owner.length() == 0 ? "" : "\nOwner: " + owner;
        NexusDialogs.info(
            "CAD: " + MiniJson.text(status, "number")
                + "\nFile: " + MiniJson.text(status, "file_name")
                + "\nRevision: " + MiniJson.text(status, "revision") + "." + MiniJson.text(status, "iteration")
                + "\nLifecycle: " + MiniJson.text(status, "lifecycle_state")
                + "\nState: " + MiniJson.text(status, "checkout_state")
                + ownerLine
                + "\nWorkspace: " + MiniJson.text(status, "checkout_workspace_name")
                + "\nEditable here: " + (MiniJson.bool(status, "can_modify") ? "Yes" : "No"),
            "Nexus CAD Status"
        );
    }

    private static void showWorkspaceStatus() throws Exception {
        Map<String, Object> workspace = requireWorkspace();
        Map<String, Object> state = loadWorkspaceState(MiniJson.text(workspace, "id"));
        List<Object> localFiles = MiniJson.array(state.get("local_files"));
        List<Object> checkouts = MiniJson.array(state.get("cad_documents"));
        List<String> lines = new ArrayList<String>();
        lines.add("Workspace: " + MiniJson.text(workspace, "name"));
        lines.add("Path: " + MiniJson.text(workspace, "path"));
        lines.add("Local Creo files: " + localFiles.size());
        lines.add("Active Nexus checkouts: " + checkouts.size());
        lines.add("");
        for (Object raw : localFiles) {
            Map<String, Object> file = MiniJson.object(raw);
            lines.add(MiniJson.text(file, "filename") + " - "
                + MiniJson.text(file, "status") + " - "
                + MiniJson.text(file, "detail"));
        }
        NexusDialogs.info(joinList(lines, "\n"), "Nexus Workspace Status");
    }

    private static void showHistory() throws Exception {
        Map<String, Object> status = resolveCurrentModel();
        Map<String, Object> result = api.cadHistory(MiniJson.integer(status, "id"));
        List<Object> history = MiniJson.array(result.get("history"));
        List<String> lines = new ArrayList<String>();
        lines.add("CAD: " + MiniJson.text(status, "file_name"));
        lines.add("Current revision: " + MiniJson.text(status, "revision") + "."
            + MiniJson.integer(status, "iteration"));
        lines.add("");
        for (Object raw : history) {
            Map<String, Object> entry = MiniJson.object(raw);
            String action = MiniJson.text(entry, "action");
            String timestamp = MiniJson.text(entry, "created_at");
            String user = MiniJson.text(entry, "username");
            if (user.length() == 0) user = MiniJson.text(entry, "user_id");
            String note = MiniJson.text(entry, "note");
            String workspaceName = MiniJson.text(entry, "workspace_name");
            String line = timestamp + "  " + action;
            if (user.length() > 0) line += "  by " + user;
            if (workspaceName.length() > 0) line += "  [" + workspaceName + "]";
            if (note.length() > 0) line += "\n  " + note;
            lines.add(line);
        }
        if (history.isEmpty()) lines.add("No checkout history is recorded.");
        NexusDialogs.info(joinList(lines, "\n"), "Nexus CAD History");
    }

    private static void reviseCurrent() throws Exception {
        Map<String, Object> status = resolveCurrentModel();
        if (MiniJson.bool(status, "can_modify")) {
            throw new IllegalStateException(
                "Undo or check in the active checkout before creating a new CAD revision."
            );
        }
        boolean confirmed = NexusDialogs.confirm(
            "Create the next CAD revision for " + MiniJson.text(status, "file_name") + "?",
            "Nexus CAD Revision",
            JOptionPane.QUESTION_MESSAGE
        );
        if (!confirmed) return;
        Map<String, Object> result = api.revise(MiniJson.integer(status, "id"));
        Map<String, Object> revised = MiniJson.object(result.get("cad"));
        NexusDialogs.info(
            "New CAD revision created: " + MiniJson.text(revised, "revision") + "."
                + MiniJson.integer(revised, "iteration")
                + "\nCheck it out before editing.",
            "Nexus CAD Revision"
        );
    }

    private static void releaseCurrent() throws Exception {
        Map<String, Object> status = resolveCurrentModel();
        if (MiniJson.text(status, "checkout_state").length() > 0
            && !"CHECKED_IN".equals(MiniJson.text(status, "checkout_state"))) {
            throw new IllegalStateException(
                "Check in or undo the active checkout before releasing this CAD Document."
            );
        }
        boolean confirmed = NexusDialogs.confirm(
            "Release " + MiniJson.text(status, "file_name") + " at revision "
                + MiniJson.text(status, "revision") + "." + MiniJson.integer(status, "iteration") + "?",
            "Nexus Release CAD",
            JOptionPane.QUESTION_MESSAGE
        );
        if (!confirmed) return;
        Map<String, Object> result = api.release(MiniJson.integer(status, "id"));
        Map<String, Object> released = MiniJson.object(result.get("cad"));
        NexusDialogs.info(
            "CAD Document released at revision " + MiniJson.text(released, "revision")
                + "." + MiniJson.integer(released, "iteration") + ".",
            "Nexus Release CAD"
        );
    }

    private static NexusDialogs.ConflictItem conflictItem(Map<String, Object> conflict) {
        List<Object> rawActions = MiniJson.array(conflict.get("actions"));
        String[] actionCodes = new String[rawActions.size()];
        String[] actionLabels = new String[rawActions.size()];
        for (int index = 0; index < rawActions.size(); index++) {
            Map<String, Object> action = MiniJson.object(rawActions.get(index));
            actionCodes[index] = MiniJson.text(action, "code");
            actionLabels[index] = MiniJson.text(action, "label");
        }
        List<String> context = new ArrayList<String>();
        String owner = MiniJson.text(conflict, "owner");
        String workspace = MiniJson.text(conflict, "workspace");
        String revision = MiniJson.text(conflict, "revision");
        int iteration = MiniJson.integer(conflict, "iteration");
        if (owner.length() > 0) context.add("Owner: " + owner);
        if (workspace.length() > 0) context.add("Workspace: " + workspace);
        if (revision.length() > 0 || iteration > 0) {
            context.add("Revision: " + revision + "." + iteration);
        }
        return new NexusDialogs.ConflictItem(
            MiniJson.text(conflict, "id"),
            MiniJson.text(conflict, "object"),
            MiniJson.text(conflict, "description"),
            MiniJson.text(conflict, "name"),
            joinList(context, " | "),
            MiniJson.text(conflict, "severity"),
            actionCodes,
            actionLabels,
            MiniJson.text(conflict, "default_action")
        );
    }

    private static NexusDialogs.ConflictResult showConflicts(
        List<Object> conflicts, String title
    ) {
        NexusDialogs.ConflictItem[] items =
            new NexusDialogs.ConflictItem[conflicts.size()];
        for (int index = 0; index < conflicts.size(); index++) {
            items[index] = conflictItem(MiniJson.object(conflicts.get(index)));
        }
        return NexusDialogs.conflicts(title, items);
    }

    private static String chooseEditConflictAction(
        Map<String, Object> status, String title
    ) {
        List<Object> conflicts = MiniJson.array(status.get("edit_conflicts"));
        if (conflicts.isEmpty()) return "CANCEL";
        NexusDialogs.ConflictResult resolution = showConflicts(conflicts, title);
        if (resolution == null) return "CANCEL";
        Map<String, Object> conflict = MiniJson.object(conflicts.get(0));
        return resolution.actionFor(MiniJson.text(conflict, "id"));
    }

    private static boolean applyEditConflictAction(
        String action,
        Map<String, Object> status,
        Model model,
        boolean preserveLocalChanges
    ) throws Exception {
        if ("CONTINUE_LOCALLY".equals(action)) {
            Map<String, Object> workspace = requireWorkspace();
            if (!modelMatchesWorkspace(
                model,
                MiniJson.text(workspace, "path"),
                MiniJson.text(status, "file_name")
            )) {
                throw new IllegalStateException(
                    "Open the CAD Document from its selected Nexus workspace before continuing locally."
                );
            }
            api.setEditIntent(
                MiniJson.integer(status, "id"),
                MiniJson.text(workspace, "id"),
                preserveLocalChanges
                    ? "Creo detected a local modification before checkout."
                    : "User chose to continue locally."
            );
            markLocalDraft(model);
            return true;
        }
        if ("CHECKOUT_NOW".equals(action) || "REVISE_AND_CHECKOUT".equals(action)) {
            Map<String, Object> workspace = requireWorkspace();
            boolean retained = checkoutCurrentModel(
                status,
                model,
                workspace,
                preserveLocalChanges,
                false,
                "REVISE_AND_CHECKOUT".equals(action)
            );
            return retained;
        }
        return false;
    }

    static boolean beginEditCommand() throws Exception {
        Model model = currentOrActiveModel();
        if (!resolveModelConflict(model, false)) return false;
        if (model != null) {
            Set<String> authorized = authorizedEditModels.get();
            if (authorized == null) {
                authorized = new LinkedHashSet<String>();
                authorizedEditModels.set(authorized);
            }
            authorized.add(modelGuardKey(model));
        }
        return true;
    }

    static void endEditCommand() {
        authorizedEditModels.remove();
    }

    static void beginModelRegeneration(Model model) {
        String key = modelGuardKey(model);
        if (key.length() == 0) return;
        synchronized (modelRegenerationDepth) {
            Integer depth = modelRegenerationDepth.get(key);
            modelRegenerationDepth.put(key, Integer.valueOf(
                depth == null ? 1 : depth.intValue() + 1
            ));
        }
    }

    static void endModelRegeneration(Model model) {
        String key = modelGuardKey(model);
        if (key.length() == 0) return;
        synchronized (modelRegenerationDepth) {
            Integer depth = modelRegenerationDepth.get(key);
            if (depth == null || depth.intValue() <= 1) {
                modelRegenerationDepth.remove(key);
            } else {
                modelRegenerationDepth.put(key, Integer.valueOf(depth.intValue() - 1));
            }
        }
    }

    private static boolean isModelRegenerating(Model model) {
        String key = modelGuardKey(model);
        if (key.length() == 0) return false;
        synchronized (modelRegenerationDepth) {
            return modelRegenerationDepth.containsKey(key);
        }
    }

    private static boolean isAuthorizedEditCommand(Model model) {
        Set<String> authorized = authorizedEditModels.get();
        return authorized != null && authorized.contains(modelGuardKey(model));
    }

    private static String modelGuardKey(Model model) {
        if (model == null) return "";
        try {
            String fileName = logicalCreoFileName(model.GetFileName()).toLowerCase();
            String origin = model.GetOrigin();
            String path = origin == null ? "" : origin.replace('/', '\\').toLowerCase();
            return path + "|" + fileName;
        } catch (Throwable ignored) {
            return "";
        }
    }

    static void requireModelEdit(Model model, String operation) throws jxthrowable {
        try {
            // The edit command checks authorization once; regeneration is not a user edit.
            if (isAuthorizedEditCommand(model) || isModelRegenerating(model)) return;
            boolean preserveLocalChanges = false;
            try {
                preserveLocalChanges = model != null && model.GetIsModified();
            } catch (Throwable ignored) {
            }
            if (resolveModelConflict(model, preserveLocalChanges)) return;
            XCancelProEAction.Throw();
        } catch (jxthrowable error) {
            throw error;
        } catch (Throwable error) {
            String detail = error.getMessage() == null
                ? String.valueOf(error)
                : error.getMessage();
            NexusDialogs.error(
                "Nexus could not authorize " + operation + ".\n\n" + detail,
                "Nexus Edit Conflict"
            );
            XCancelProEAction.Throw();
        }
    }

    static Model modelForChild(Child child) {
        if (child == null) return currentOrActiveModel();
        try {
            Parent parent = child.GetDBParent();
            for (int depth = 0; parent != null && depth < 12; depth++) {
                if (parent instanceof Model) return (Model) parent;
                if (!(parent instanceof Child)) break;
                parent = ((Child) parent).GetDBParent();
            }
        } catch (Throwable ignored) {
        }
        return currentOrActiveModel();
    }

    static boolean resolveModelConflict(Model model, boolean preserveLocalChanges)
        throws Exception {
        if (model == null || api == null) return true;
        if (isLocalDraftModel(model)) return true;
        Map<String, Object> status = resolveCadForModel(model);
        if (!MiniJson.bool(status, "managed")
            || MiniJson.bool(status, "can_modify")) {
            return true;
        }
        if (MiniJson.bool(status, "local_edit_intent")
            && selectedWorkspace != null
            && modelMatchesWorkspace(
                model,
                MiniJson.text(selectedWorkspace, "path"),
                MiniJson.text(status, "file_name")
            )) {
            markLocalDraft(model);
            return true;
        }
        return applyEditConflictAction(
            chooseEditConflictAction(status, "Conflicts"),
            status,
            model,
            preserveLocalChanges
        );
    }

    private static void checkout() throws Exception {
        Map<String, Object> workspace = requireWorkspace();
        installCommandGuards();
        Model current = requireCurrentModel();
        Map<String, Object> status = resolveCadForModel(current);
        if (!MiniJson.bool(status, "managed")) {
            if (!registerUnmanagedWorkspaceModel(current, workspace)) return;
            activateModel(current);
            NexusDialogs.info(
                "The new CAD file was registered and checked out in the selected Nexus workspace.",
                "Nexus Check Out"
            );
            return;
        }
        requireManaged(status);
        boolean currentIsWorkspaceModel = modelMatchesWorkspace(
            current,
            MiniJson.text(workspace, "path"),
            MiniJson.text(status, "file_name")
        );
        if (MiniJson.bool(status, "can_modify")) {
            String checkoutWorkspaceId = MiniJson.text(status, "checkout_workspace_id");
            if (!MiniJson.text(workspace, "id").equalsIgnoreCase(checkoutWorkspaceId)) {
                throw new IllegalStateException(
                    "This CAD Document is already checked out in workspace "
                        + MiniJson.text(status, "checkout_workspace_name")
                        + ". Select that CAD workspace before reopening it."
                );
            }
            if (currentIsWorkspaceModel) {
                activateModel(current);
            } else {
                ensureReloadIsSafe(current, workspace, false);
                Map<String, Object> existing = api.retrieve(
                    MiniJson.integer(status, "id"), MiniJson.text(workspace, "id")
                );
                eraseForReload(current);
                displayManagedResult(existing);
            }
            NexusDialogs.info(
                "The existing checkout was reopened from its Nexus CAD workspace.",
                "Nexus Check Out"
            );
            return;
        }
        if ("CHECKED_OUT_BY_OTHER".equals(MiniJson.text(status, "checkout_state"))) {
            String action = chooseEditConflictAction(status, "Conflicts");
            if ("CONTINUE_LOCALLY".equals(action)) {
                applyEditConflictAction(action, status, current, false);
                NexusDialogs.info(
                    "Local edit intent is active. Nexus check-in remains blocked until "
                        + "you obtain the checkout.",
                    "Nexus Conflict Management"
                );
            }
            return;
        }
        boolean preserveLocalChanges = MiniJson.bool(status, "local_edit_intent");
        if (preserveLocalChanges) {
            boolean adopt = NexusDialogs.confirm(
                "A local edit intent is active. Adopt the local changes into the Nexus checkout?",
                "Nexus Check Out",
                JOptionPane.QUESTION_MESSAGE
            );
            if (!adopt) return;
        }
        ensureReloadIsSafe(current, workspace, preserveLocalChanges);
        boolean reviseReleased = false;
        if ("RELEASED".equals(MiniJson.text(status, "lifecycle_state"))) {
            String action = chooseEditConflictAction(status, "Conflicts");
            if ("CONTINUE_LOCALLY".equals(action)) {
                applyEditConflictAction(action, status, current, false);
                NexusDialogs.info(
                    "Local edit intent is active. Nexus check-in remains blocked until "
                        + "the released CAD Document is revised and checked out.",
                    "Nexus Conflict Management"
                );
                return;
            }
            if (!"REVISE_AND_CHECKOUT".equals(action)) return;
            reviseReleased = true;
        }

        Map<String, Object> result = null;
        String itemRevision = null;
        boolean itemRevisionPrompted = false;
        while (result == null) {
            try {
                result = api.checkout(
                    MiniJson.integer(status, "id"),
                    MiniJson.text(workspace, "id"),
                    reviseReleased,
                    itemRevision,
                    preserveLocalChanges
                );
            } catch (NexusApiException error) {
                if ("cad_revision_required".equals(error.getCode()) && !reviseReleased) {
                    boolean confirmed = NexusDialogs.confirm(
                        error.getMessage() + "\n\nContinue and revise the affected CAD Documents?",
                        "Nexus CAD Revision",
                        JOptionPane.QUESTION_MESSAGE
                    );
                    if (!confirmed) return;
                    reviseReleased = true;
                    continue;
                }
                if ("item_revision_required".equals(error.getCode()) && !itemRevisionPrompted) {
                    itemRevisionPrompted = true;
                    String revision = NexusDialogs.input(
                        "An associated Item is Released. Enter its next revision (for example B):",
                        "Nexus Item Revision"
                    );
                    if (revision == null || revision.trim().length() == 0) return;
                    itemRevision = revision.trim();
                    continue;
                }
                throw error;
            }
        }

        if (currentIsWorkspaceModel) {
            localDraftModels.remove(localDraftKey(current));
            activateModel(current);
        } else {
            eraseForReload(current);
            displayManagedResult(result);
        }
        NexusDialogs.info(
            "CAD checkout completed. The managed workspace copy is now editable.",
            "Nexus Check Out"
        );
    }

    private static boolean checkoutCurrentModel(
        Map<String, Object> status,
        Model current,
        Map<String, Object> workspace,
        boolean preserveLocalChanges,
        boolean notify,
        boolean reviseReleasedConfirmed
    ) throws Exception {
        if ("CHECKED_OUT_BY_OTHER".equals(MiniJson.text(status, "checkout_state"))) {
            throw new IllegalStateException(
                "This CAD Document is checked out by "
                    + (MiniJson.text(status, "checked_out_by_username").length() == 0
                        ? "another Nexus user"
                        : MiniJson.text(status, "checked_out_by_username")) + "."
            );
        }
        boolean retained = modelMatchesWorkspace(
            current,
            MiniJson.text(workspace, "path"),
            MiniJson.text(status, "file_name")
        );
        if (!retained) {
            throw new IllegalStateException(
                "Conflict Management will not replace or close the active Creo model. "
                    + "Open its managed copy from the selected Nexus workspace, then retry."
            );
        }
        boolean reviseReleased = reviseReleasedConfirmed;
        if ("RELEASED".equals(MiniJson.text(status, "lifecycle_state"))
            && !reviseReleased) {
            boolean confirmed = NexusDialogs.confirm(
                "This CAD Document is Released. Create its next CAD revision and check it out?",
                "Nexus Check Out",
                JOptionPane.QUESTION_MESSAGE
            );
            if (!confirmed) return false;
            reviseReleased = true;
        }
        Map<String, Object> result = null;
        String itemRevision = null;
        boolean itemRevisionPrompted = false;
        while (result == null) {
            try {
                result = api.checkout(
                    MiniJson.integer(status, "id"),
                    MiniJson.text(workspace, "id"),
                    reviseReleased,
                    itemRevision,
                    preserveLocalChanges
                );
            } catch (NexusApiException error) {
                if ("cad_revision_required".equals(error.getCode()) && !reviseReleased) {
                    boolean confirmed = NexusDialogs.confirm(
                        error.getMessage() + "\n\nContinue and revise the affected CAD Documents?",
                        "Nexus CAD Revision",
                        JOptionPane.QUESTION_MESSAGE
                    );
                    if (!confirmed) return false;
                    reviseReleased = true;
                    continue;
                }
                if ("item_revision_required".equals(error.getCode()) && !itemRevisionPrompted) {
                    itemRevisionPrompted = true;
                    String revision = NexusDialogs.input(
                        "An associated Item is Released. Enter its next revision (for example B):",
                        "Nexus Item Revision"
                    );
                    if (revision == null || revision.trim().length() == 0) return false;
                    itemRevision = revision.trim();
                    continue;
                }
                throw error;
            }
        }
        localDraftModels.remove(localDraftKey(current));
        // Conflict resolution must never erase, retrieve, display, activate, or
        // switch models. The original Creo command continues in the same window.
        if (notify) {
            NexusDialogs.info(
                "CAD checkout completed. The managed workspace copy is now editable.",
                "Nexus Check Out"
            );
        }
        return true;
    }

    private static void checkin() throws Exception {
        Map<String, Object> workspace = requireWorkspace();
        List<CheckinChoice> choices = checkinChoices(workspace);
        if (choices.isEmpty()) {
            NexusDialogs.info(
                "No managed CAD Documents are available for check-in in this workspace.",
                "Nexus Check In"
            );
            return;
        }
        Object[] labels = new Object[choices.size()];
        boolean[] selected = new boolean[choices.size()];
        for (int index = 0; index < choices.size(); index++) {
            CheckinChoice choice = choices.get(index);
            labels[index] = choice.checklistItem();
            selected[index] = choice.defaultSelected();
        }
        NexusDialogs.ChecklistResult selection = NexusDialogs.checklist(
            "Workspace: " + MiniJson.text(workspace, "name")
                + "    Select parts, assemblies, and drawings to check in:",
            "Nexus Check In",
            labels,
            selected,
            "Check-in comment:"
        );
        if (selection == null) return;
        String note = selection.note.trim();
        if (note.length() == 0) return;
        if (selection.selectedIndexes.length == 0) {
            NexusDialogs.info("No CAD Documents were selected.", "Nexus Check In");
            return;
        }

        Map<Integer, CheckinChoice> allChoices =
            new LinkedHashMap<Integer, CheckinChoice>();
        for (CheckinChoice choice : choices) {
            int cadId = MiniJson.integer(choice.status, "id");
            if (cadId > 0) allChoices.put(Integer.valueOf(cadId), choice);
        }
        Map<Integer, CheckinChoice> selectedChoices =
            new LinkedHashMap<Integer, CheckinChoice>();
        List<CheckinChoice> selectedNewCandidates = new ArrayList<CheckinChoice>();
        for (int index = 0; index < selection.selectedIndexes.length; index++) {
            CheckinChoice choice = choices.get(selection.selectedIndexes[index]);
            if (choice.isNewCadCandidate()) {
                selectedNewCandidates.add(choice);
                continue;
            }
            int cadId = MiniJson.integer(choice.status, "id");
            if (cadId > 0) selectedChoices.put(Integer.valueOf(cadId), choice);
        }
        if (selectedChoices.isEmpty() && selectedNewCandidates.isEmpty()) {
            NexusDialogs.info("No CAD Documents were selected.", "Nexus Check In");
            return;
        }
        Map<String, Object> creoMetadata = captureCreoStructure(
            selectedNewCandidates, selectedChoices
        );
        if (creoMetadata == null) return;
        Map<String, Object> detectedDrawingModels = MiniJson.object(
            creoMetadata.get("drawing_models_by_file")
        );
        boolean addedDrawingOwner = false;
        for (CheckinChoice drawing : new ArrayList<CheckinChoice>(selectedNewCandidates)) {
            Object rawNames = detectedDrawingModels.get(drawing.fileName().toLowerCase());
            List<Object> ownerNames = MiniJson.array(rawNames);
            if (!isDrawingChoice(drawing) || ownerNames.size() != 1) continue;
            String ownerName = String.valueOf(ownerNames.get(0));
            boolean alreadySelected = false;
            for (CheckinChoice selectedChoice : selectedNewCandidates) {
                if (selectedChoice.fileName().equalsIgnoreCase(ownerName)) {
                    alreadySelected = true;
                    break;
                }
            }
            if (alreadySelected || isCadNameManaged(ownerName, workspace)) continue;
            CheckinChoice ownerCandidate = null;
            for (CheckinChoice choice : choices) {
                if (choice.isNewCadCandidate()
                    && choice.fileName().equalsIgnoreCase(ownerName)
                    && choice.localFile != null
                    && choice.loaded != null
                    && loadedModelMatchesWorkspaceFile(
                        choice.loaded.model, MiniJson.text(choice.localFile, "path")
                    )) {
                    ownerCandidate = choice;
                    break;
                }
            }
            if (ownerCandidate == null) {
                throw new IllegalStateException(
                    "The new drawing references unregistered model " + ownerName
                        + ", but that model is not an unmapped file in the selected Nexus workspace."
                );
            }
            selectedNewCandidates.add(ownerCandidate);
            addedDrawingOwner = true;
        }
        if (addedDrawingOwner) {
            creoMetadata = captureCreoStructure(selectedNewCandidates, selectedChoices);
            if (creoMetadata == null) return;
        }
        if (!selectedNewCandidates.isEmpty()) {
            StringBuilder prompt = new StringBuilder();
            prompt.append("Nexus will create CAD Document records and check out these new Creo files before staging them in Pending:\n\n");
            List<Map<String, Object>> files = new ArrayList<Map<String, Object>>();
            for (CheckinChoice choice : selectedNewCandidates) {
                Map<String, Object> file = new LinkedHashMap<String, Object>();
                file.put("filename", MiniJson.text(choice.localFile, "filename"));
                file.put("path", MiniJson.text(choice.localFile, "path"));
                Map<String, Object> drawingModels = MiniJson.object(
                    creoMetadata.get("drawing_models_by_file")
                );
                Object related = drawingModels.get(choice.fileName().toLowerCase());
                if (related instanceof List) file.put("drawing_models", related);
                files.add(file);
                prompt.append("  ").append(choice.fileName()).append("\n");
            }
            prompt.append("\nThe CAD files stay in this workspace. Continue?");
            if (!NexusDialogs.confirm(
                prompt.toString(), "Register New CAD Documents", JOptionPane.QUESTION_MESSAGE
            )) return;
            Map<String, Object> registration = api.registerNewCad(
                MiniJson.text(workspace, "id"), files
            );
            List<Object> registered = MiniJson.array(registration.get("registered"));
            if (registered.size() != selectedNewCandidates.size()) {
                throw new IllegalStateException("Nexus did not register every selected new CAD model.");
            }
            for (int index = 0; index < registered.size(); index++) {
                CheckinChoice choice = selectedNewCandidates.get(index);
                Map<String, Object> row = MiniJson.object(registered.get(index));
                choice.status = MiniJson.object(row.get("cad"));
                choice.localFile = MiniJson.object(row.get("local_file"));
                choice.workspaceCheckout = true;
                int cadId = MiniJson.integer(choice.status, "id");
                if (cadId <= 0) {
                    throw new IllegalStateException("Nexus registered a new CAD model without an identity.");
                }
                selectedChoices.put(Integer.valueOf(cadId), choice);
                allChoices.put(Integer.valueOf(cadId), choice);
            }
        }
        Map<Integer, String> duplicateActions = new LinkedHashMap<Integer, String>();
        Map<String, String> resolvedConflictActions =
            new LinkedHashMap<String, String>();
        List<String> skipped = new ArrayList<String>();
        Map<String, Object> plan = new LinkedHashMap<String, Object>();
        for (int pass = 0; pass < 8; pass++) {
            List<Integer> selectedCadIds =
                new ArrayList<Integer>(selectedChoices.keySet());
            plan = api.checkinPlan(selectedCadIds, MiniJson.text(workspace, "id"));
            List<Object> planConflicts = MiniJson.array(plan.get("pdm_conflicts"));
            List<Object> unresolved = new ArrayList<Object>();
            for (Object value : planConflicts) {
                Map<String, Object> conflict = MiniJson.object(value);
                if (!resolvedConflictActions.containsKey(MiniJson.text(conflict, "id"))) {
                    unresolved.add(conflict);
                }
            }
            if (unresolved.isEmpty()) break;
            NexusDialogs.ConflictResult resolution = showConflicts(unresolved, "Conflicts");
            if (resolution == null) return;
            boolean selectionChanged = false;
            for (Object value : unresolved) {
                Map<String, Object> conflict = MiniJson.object(value);
                String conflictId = MiniJson.text(conflict, "id");
                String action = resolution.actionFor(conflictId);
                resolvedConflictActions.put(conflictId, action);
                int cadId = MiniJson.integer(conflict, "cad_document_id");
                CheckinChoice choice = selectedChoices.get(Integer.valueOf(cadId));
                if ("CANCEL".equals(action) || action.length() == 0) return;
                if ("SKIP_OBJECT".equals(action)) {
                    selectedChoices.remove(Integer.valueOf(cadId));
                    resolvedConflictActions.clear();
                    selectionChanged = true;
                    if (choice != null && !skipped.contains(choice.fileName())) {
                        skipped.add(choice.fileName());
                    }
                    continue;
                }
                if ("REPLACE_PENDING".equals(action)) {
                    duplicateActions.put(Integer.valueOf(cadId), "replace");
                    continue;
                }
                if ("ADD_REQUIRED_OBJECTS".equals(action)) {
                    for (Object related : MiniJson.array(
                        conflict.get("related_cad_document_ids")
                    )) {
                        int relatedId = jsonInteger(related);
                        CheckinChoice relatedChoice = allChoices.get(
                            Integer.valueOf(relatedId)
                        );
                        if (relatedChoice == null
                            || !MiniJson.bool(relatedChoice.status, "can_checkin")) {
                            throw new IllegalStateException(
                                "A required modified dependency is not eligible for check-in: "
                                    + relatedId
                            );
                        }
                        if (!selectedChoices.containsKey(Integer.valueOf(relatedId))) {
                            selectedChoices.put(Integer.valueOf(relatedId), relatedChoice);
                            selectionChanged = true;
                        }
                    }
                    continue;
                }
                if ("CHECKOUT_REQUIRED_MODEL".equals(action)) {
                    List<Object> relatedIds = MiniJson.array(
                        conflict.get("related_cad_document_ids")
                    );
                    if (relatedIds.isEmpty()) {
                        throw new IllegalStateException(
                            "Nexus did not identify the drawing's related CAD model."
                        );
                    }
                    int relatedId = jsonInteger(relatedIds.get(0));
                    CheckinChoice relatedChoice = allChoices.get(Integer.valueOf(relatedId));
                    if (relatedChoice == null || relatedChoice.localFile == null) {
                        throw new IllegalStateException(
                            "The related model is not present in this Nexus workspace. Retrieve it before checking in the drawing."
                        );
                    }
                    api.checkout(relatedId, MiniJson.text(workspace, "id"), false, null, false);
                    relatedChoice.status = api.cadStatus(relatedId);
                    Map<String, Object> refreshed = loadWorkspaceState(
                        MiniJson.text(workspace, "id")
                    );
                    for (Object localValue : MiniJson.array(refreshed.get("local_files"))) {
                        Map<String, Object> local = MiniJson.object(localValue);
                        if (MiniJson.integer(local, "cad_document_id") == relatedId) {
                            relatedChoice.localFile = local;
                            break;
                        }
                    }
                    relatedChoice.workspaceCheckout = true;
                    selectedChoices.put(Integer.valueOf(relatedId), relatedChoice);
                    resolvedConflictActions.clear();
                    selectionChanged = true;
                    continue;
                }
                throw new IllegalStateException(
                    "Unsupported check-in conflict action: " + action
                );
            }
            if (!selectionChanged) break;
        }
        if (selectedChoices.isEmpty()) {
            NexusDialogs.info(
                "No CAD Documents remain selected after conflict resolution.",
                "Nexus Check In"
            );
            return;
        }

        List<Object> pendingCommits = MiniJson.array(plan.get("pending_commits"));
        String targetCommitId = "";
        if (!pendingCommits.isEmpty()) {
            Object[] pendingOptions = new Object[pendingCommits.size() + 1];
            pendingOptions[0] = "Create a new pending commit";
            for (int index = 0; index < pendingCommits.size(); index++) {
                Map<String, Object> pending = MiniJson.object(pendingCommits.get(index));
                pendingOptions[index + 1] = "Add to: "
                    + MiniJson.text(pending, "title")
                    + " [" + MiniJson.text(pending, "commit_id") + "]";
            }
            Object selectedPending = NexusDialogs.choose(
                "You already have a Pending commit. Add these models to it or create a new commit:",
                "Nexus Pending Commit",
                pendingOptions,
                pendingOptions[1]
            );
            if (selectedPending == null) return;
            for (int index = 1; index < pendingOptions.length; index++) {
                if (pendingOptions[index].equals(selectedPending)) {
                    targetCommitId = MiniJson.text(
                        MiniJson.object(pendingCommits.get(index - 1)), "commit_id"
                    );
                    break;
                }
            }
        }

        List<Object> reviewedDependencies = new ArrayList<Object>();
        Map<String, Object> reviewSnapshot = filterCreoStructure(
            creoMetadata, selectedNewCandidates, selectedChoices
        );
        Map<String, Object> reviewStructure = MiniJson.object(reviewSnapshot.get("structure"));
        if (!MiniJson.array(reviewStructure.get("members")).isEmpty()
            || !MiniJson.array(reviewStructure.get("drawings")).isEmpty()
            || !MiniJson.array(reviewStructure.get("complete_assemblies")).isEmpty()) {
            Map<String, Object> review = api.reviewCadStructure(
                new ArrayList<Integer>(selectedChoices.keySet()), reviewStructure
            );
            reviewedDependencies = MiniJson.array(review.get("dependencies"));
            if (!confirmStructureReview(review)) return;
        }

        List<CheckinChoice> stagingChoices = new ArrayList<CheckinChoice>();
        for (CheckinChoice choice : selectedChoices.values()) {
            if (!isDrawingChoice(choice)) stagingChoices.add(choice);
        }
        for (CheckinChoice choice : selectedChoices.values()) {
            if (isDrawingChoice(choice)) stagingChoices.add(choice);
        }
        List<Integer> checkinBatch = new ArrayList<Integer>(selectedChoices.keySet());
        List<String> staged = new ArrayList<String>();
        List<Integer> stagedCadIds = new ArrayList<Integer>();
        Map<Integer, CheckinChoice> stagedChoices = new LinkedHashMap<Integer, CheckinChoice>();
        for (CheckinChoice choice : stagingChoices) {
            if (!MiniJson.bool(choice.status, "can_checkin")) {
                String reason = MiniJson.text(choice.status, "read_only_reason");
                throw new IllegalStateException(
                    choice.fileName() + " cannot be checked in."
                        + (reason.length() == 0 ? "" : "\n" + reason)
                );
            }
            int cadId = MiniJson.integer(choice.status, "id");
            String duplicateAction = duplicateActions.containsKey(Integer.valueOf(cadId))
                ? duplicateActions.get(Integer.valueOf(cadId))
                : "error";
            if (choice.loaded != null && choice.loaded.model.GetIsModified()) {
                choice.loaded.model.Save();
            }
            Map<String, Object> result = api.checkin(
                cadId,
                MiniJson.text(choice.status, "checkout_workspace_id"),
                "",
                note,
                targetCommitId,
                duplicateAction,
                checkinBatch
            );
            Map<String, Object> pending = MiniJson.object(result.get("pending_commit"));
            if (targetCommitId.length() == 0) {
                targetCommitId = MiniJson.text(pending, "commit_id");
            }
            boolean wasSkipped = false;
            for (Object skippedFile : MiniJson.array(pending.get("skipped_files"))) {
                if (choice.fileName().equalsIgnoreCase(String.valueOf(skippedFile))) {
                    wasSkipped = true;
                    break;
                }
            }
            if (wasSkipped) {
                if (!skipped.contains(choice.fileName())) skipped.add(choice.fileName());
            } else {
                staged.add(choice.fileName());
                stagedCadIds.add(Integer.valueOf(cadId));
                stagedChoices.put(Integer.valueOf(cadId), choice);
            }
        }
        String skippedText = skipped.isEmpty()
            ? ""
            : "\n\nSkipped:\n" + joinList(skipped, "\n");
        if (staged.isEmpty()) {
            NexusDialogs.info(
                "No CAD files were staged." + skippedText,
                "Nexus Check In"
            );
            return;
        }
        String structureNotice = "";
        if (targetCommitId.length() > 0 && creoMetadata != null) {
            Map<String, Object> stagedStructure = filterCreoStructure(
                creoMetadata, selectedNewCandidates, stagedChoices
            );
            Map<String, Object> structure = MiniJson.object(stagedStructure.get("structure"));
            structure.put("dependency_baselines", reviewedDependencies);
            if (!MiniJson.array(structure.get("members")).isEmpty()
                || !MiniJson.array(structure.get("drawings")).isEmpty()
                || !MiniJson.array(structure.get("complete_assemblies")).isEmpty()) {
                Map<String, Object> structureResult = api.stageCadStructure(
                    targetCommitId, stagedCadIds, structure
                );
                if (MiniJson.bool(structureResult, "staged")) {
                    structureNotice = "\nCreo assembly and drawing relationships are attached to this Pending commit and will be applied after approval.";
                }
            }
        }
        NexusDialogs.info(
            "CAD files staged in Pending commit " + targetCommitId + ":\n"
                + joinList(staged, "\n") + skippedText
                + "\n\nThe CAD and associated Item checkouts remain active until approval and merge."
                + structureNotice,
            "Nexus Check In"
        );
    }

    private static boolean isDrawingChoice(CheckinChoice choice) {
        String category = MiniJson.text(choice.status, "category");
        return "DRAWING".equalsIgnoreCase(category)
            || choice.fileName().toLowerCase().endsWith(".drw");
    }

    private static List<CheckinChoice> checkinChoices(Map<String, Object> workspace)
        throws Exception {
        List<CheckinChoice> choices = new ArrayList<CheckinChoice>();
        Map<Integer, CheckinChoice> byId = new LinkedHashMap<Integer, CheckinChoice>();
        Map<String, Object> workspaceState = loadWorkspaceState(MiniJson.text(workspace, "id"));
        List<Object> localRows = MiniJson.array(workspaceState.get("local_files"));
        for (Object item : localRows) {
            Map<String, Object> local = MiniJson.object(item);
            int cadId = MiniJson.integer(local, "cad_document_id");
            Map<String, Object> status = MiniJson.object(item);
            if (cadId > 0) {
                try {
                    status = api.cadStatus(cadId);
                } catch (Exception ignored) {
                    status = new LinkedHashMap<String, Object>();
                }
            } else if ("UNMAPPED".equalsIgnoreCase(MiniJson.text(local, "status"))
                && isNewModelFile(MiniJson.text(local, "logical_file_name"))) {
                status = new LinkedHashMap<String, Object>(local);
                status.put("file_name", MiniJson.text(local, "logical_file_name"));
                String extensionName = MiniJson.text(local, "logical_file_name").toLowerCase();
                status.put("category", extensionName.endsWith(".asm") ? "ASSEMBLY"
                    : extensionName.endsWith(".drw") ? "DRAWING" : "COMPONENT");
                status.put("new_candidate", Boolean.TRUE);
            }
            CheckinChoice choice = new CheckinChoice(status);
            choice.localFile = local;
            choices.add(choice);
            if (cadId > 0) {
                byId.put(Integer.valueOf(cadId), choice);
            }
        }

        List<Object> workspaceRows = MiniJson.array(workspaceState.get("cad_documents"));
        for (Object item : workspaceRows) {
            Map<String, Object> status = MiniJson.object(item);
            if (!MiniJson.bool(status, "managed")) continue;
            Integer id = Integer.valueOf(MiniJson.integer(status, "id"));
            CheckinChoice choice = byId.get(id);
            if (choice == null) {
                choice = new CheckinChoice(status);
                choices.add(choice);
                byId.put(id, choice);
            } else {
                choice.status = status;
            }
            choice.workspaceCheckout = true;
        }

        Map<String, LoadedModelInfo> loaded = loadedCreoModels();
        Models sessionModels = session.ListModels();
        for (LoadedModelInfo info : loaded.values()) {
            Map<String, Object> status;
            try {
                status = resolveCadForModel(info.model);
            } catch (Exception ignored) {
                continue;
            }
            if (!MiniJson.bool(status, "managed")) {
                String modelName = "";
                try {
                    modelName = logicalModelFileName(info.model).toLowerCase();
                } catch (Exception ignored) {
                }
                for (CheckinChoice choice : choices) {
                    if (choice.isNewCadCandidate()
                        && choice.fileName().toLowerCase().equals(modelName)
                        && loadedModelMatchesWorkspaceFile(
                            info.model, MiniJson.text(choice.localFile, "path")
                        )) {
                        choice.loaded = info;
                        break;
                    }
                }
                continue;
            }
            Integer id = Integer.valueOf(MiniJson.integer(status, "id"));
            CheckinChoice choice = byId.get(id);
            if (choice == null) {
                choice = new CheckinChoice(status);
                choices.add(choice);
                byId.put(id, choice);
            } else {
                choice.status = status;
            }
            choice.loaded = info;
        }
        if (sessionModels != null) {
            for (CheckinChoice choice : choices) {
                if (!choice.isNewCadCandidate()) continue;
                String wanted = logicalCreoFileName(choice.fileName()).toLowerCase();
                for (int index = 0; index < sessionModels.getarraysize(); index++) {
                    Model model = sessionModels.get(index);
                    if (model != null
                        && wanted.equalsIgnoreCase(logicalModelFileName(model))
                        && loadedModelMatchesWorkspaceFile(
                            model, MiniJson.text(choice.localFile, "path")
                        )) {
                        choice.loaded = new LoadedModelInfo(model, "loaded session");
                        break;
                    }
                }
            }
        }
        return choices;
    }

    private static boolean isNewModelFile(String filename) {
        String value = String.valueOf(filename == null ? "" : filename).toLowerCase();
        return value.endsWith(".prt") || value.matches(".*\\.prt\\.\\d+$")
            || value.endsWith(".asm") || value.matches(".*\\.asm\\.\\d+$")
            || value.endsWith(".drw") || value.matches(".*\\.drw\\.\\d+$");
    }

    private static Map<String, Object> captureCreoStructure(
        List<CheckinChoice> candidates,
        Map<Integer, CheckinChoice> selectedChoices
    ) throws Exception {
        Set<String> selectedNames = new LinkedHashSet<String>();
        Set<String> newCandidateNames = new LinkedHashSet<String>();
        Map<String, String> selectedPaths = new LinkedHashMap<String, String>();
        for (CheckinChoice choice : candidates) {
            String name = logicalCreoFileName(choice.fileName()).toLowerCase();
            selectedNames.add(name);
            newCandidateNames.add(name);
            selectedPaths.put(name, MiniJson.text(choice.localFile, "path"));
        }
        for (CheckinChoice choice : selectedChoices.values()) {
            String name = logicalCreoFileName(choice.fileName()).toLowerCase();
            selectedNames.add(name);
            selectedPaths.put(name, MiniJson.text(choice.localFile, "path"));
        }
        Models sessionModels = session.ListModels();
        Map<String, Model> modelsByName = new LinkedHashMap<String, Model>();
        if (sessionModels != null) {
            for (int index = 0; index < sessionModels.getarraysize(); index++) {
                Model model = sessionModels.get(index);
                if (model == null) continue;
                String name = logicalModelFileName(model).toLowerCase();
                if (name.length() > 0) modelsByName.put(name, model);
            }
        }
        for (String name : newCandidateNames) {
            Model model = modelsByName.get(name);
            String targetPath = selectedPaths.get(name);

            if (model == null) {
                throw new IllegalStateException(
                    "Model missing from Nexus session lookup: " + name
                    + "\nSession model names: " + modelsByName.keySet()
                );
            }

            if (!loadedModelMatchesWorkspaceFile(model, targetPath)) {
                String origin;
                try {
                    origin = model.GetOrigin();
                } catch (Exception error) {
                    origin = "<unavailable: " + error.toString() + ">";
                }

                throw new IllegalStateException(
                    "Model found, but workspace file matching failed."
                    + "\nModel: " + name
                    + "\nModel origin: " + origin
                    + "\nExpected workspace file: " + targetPath
                );
            }
        }

        List<Object> members = new ArrayList<Object>();
        List<Object> drawings = new ArrayList<Object>();
        List<Object> completeAssemblies = new ArrayList<Object>();
        Set<String> componentChildren = new LinkedHashSet<String>();
        Map<String, Object> drawingModelsByFile = new LinkedHashMap<String, Object>();
        if (sessionModels != null) {
            for (int index = 0; index < sessionModels.getarraysize(); index++) {
                Model model = sessionModels.get(index);
                if (model == null) continue;
                String fileName = logicalModelFileName(model).toLowerCase();
                if (fileName.endsWith(".asm")) {
                    boolean familyTableInstance = isFamilyTableInstance(model);
                    if (familyTableInstance
                        && !hasSeparateFamilyInstanceIdentity(model, fileName)) {
                        if (model instanceof Solid) {
                            Features instanceFeatures = ((Solid) model).ListFeaturesByType(
                                Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT
                            );
                            if (instanceFeatures != null) {
                                for (int featureIndex = 0;
                                     featureIndex < instanceFeatures.getarraysize();
                                     featureIndex++) {
                                    ComponentFeat component = (ComponentFeat)
                                        instanceFeatures.get(featureIndex);
                                    componentChildren.add(logicalCreoFileName(
                                        component.GetModelDescr().GetFileName()
                                    ).toLowerCase());
                                }
                            }
                        }
                        continue;
                    }
                    if (!(model instanceof Solid)) {
                        if (selectedNames.contains(fileName)
                            && selectedPaths.containsKey(fileName)
                            && loadedModelMatchesWorkspaceFile(model, selectedPaths.get(fileName))) {
                            throw new IllegalStateException("Creo did not expose assembly features for " + fileName);
                        }
                        continue;
                    }
                    Features features = ((Solid) model).ListFeaturesByType(
                        Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT
                    );
                    boolean selectedAssembly = selectedNames.contains(fileName);
                    boolean workspaceAssembly = selectedPaths.containsKey(fileName)
                        && loadedModelMatchesWorkspaceFile(model, selectedPaths.get(fileName));
                    boolean workspaceDirectory = modelInSelectedWorkspaceDirectory(model, selectedPaths);
                    if (features == null) {
                        if (selectedAssembly && workspaceAssembly) {
                            throw new IllegalStateException(
                                "Creo did not return a complete component list for " + fileName
                                    + "; no assembly relationships were changed."
                            );
                        }
                        continue;
                    }
                    for (int featureIndex = 0; featureIndex < features.getarraysize(); featureIndex++) {
                        Feature feature = features.get(featureIndex);
                        ComponentFeat component = (ComponentFeat) feature;
                        String childName = logicalCreoFileName(
                            component.GetModelDescr().GetFileName()
                        ).toLowerCase();
                        if ((!selectedAssembly || !workspaceAssembly)
                            && (!selectedNames.contains(childName) || !workspaceDirectory)) continue;
                        int featureStatus = feature.GetStatus().getValue();
                        if (featureStatus != FeatureStatus._FEAT_ACTIVE
                            && familyTableInstance
                            && (featureStatus == FeatureStatus._FEAT_FAMILY_TABLE_SUPPRESSED
                                || featureStatus == FeatureStatus._FEAT_SUPPRESSED)) {
                            continue;
                        }
                        if (featureStatus != FeatureStatus._FEAT_ACTIVE) {
                            throw new IllegalStateException(
                                "Resolve the inactive or suppressed assembly component before check-in: "
                                    + fileName + " / " + childName
                            );
                        }
                        Map<String, Object> edge = new LinkedHashMap<String, Object>();
                        edge.put("parent_file_name", fileName);
                        edge.put("child_file_name", childName);
                        edge.put("feature_id", Integer.valueOf(feature.GetId()));
                        edge.put("status", "ACTIVE");
                        members.add(edge);
                        componentChildren.add(childName);
                    }
                    if (selectedAssembly && workspaceAssembly) {
                        completeAssemblies.add(fileName);
                    }
                } else if (fileName.endsWith(".drw") && model instanceof Model2D) {
                    Models references = ((Model2D) model).ListModels();
                    List<Object> names = new ArrayList<Object>();
                    if (references != null) {
                        for (int refIndex = 0; refIndex < references.getarraysize(); refIndex++) {
                            Model reference = references.get(refIndex);
                            String modelName = logicalModelFileName(reference).toLowerCase();
                            if (!modelName.endsWith(".prt") && !modelName.endsWith(".asm")) continue;
                            if (!names.contains(modelName)) names.add(modelName);
                            if (selectedNames.contains(fileName) || selectedNames.contains(modelName)) {
                                Map<String, Object> relation = new LinkedHashMap<String, Object>();
                                relation.put("drawing_file_name", fileName);
                                relation.put("model_file_name", modelName);
                                drawings.add(relation);
                            }
                        }
                    }
                    if (newCandidateNames.contains(fileName)) {
                        drawingModelsByFile.put(fileName, names);
                        if (names.size() != 1) {
                            throw new IllegalStateException(
                                "A new drawing must reference exactly one PRT or ASM model before check-in: "
                                    + fileName
                            );
                        }
                    }
                }
            }
        }
        List<String> unplacedParts = new ArrayList<String>();
        for (String name : newCandidateNames) {
            if (name.endsWith(".prt") && !componentChildren.contains(name)) {
                unplacedParts.add(name);
            }
        }
        if (!unplacedParts.isEmpty() && !NexusDialogs.confirm(
            "Creo found no loaded parent assembly for these new part(s):\n\n"
                + joinList(unplacedParts, "\n")
                + "\n\nThey will be registered as top-level CAD Documents. "
                + "If they belong under an assembly, open that assembly in this session and try again.",
            "Confirm Top-Level CAD Documents", JOptionPane.WARNING_MESSAGE
        )) {
            return null;
        }
        Map<String, Object> structure = new LinkedHashMap<String, Object>();
        structure.put("schema", Integer.valueOf(1));
        structure.put("members", members);
        structure.put("drawings", drawings);
        structure.put("complete_assemblies", completeAssemblies);
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("structure", structure);
        result.put("drawing_models_by_file", drawingModelsByFile);
        return result;
    }

    private static boolean confirmStructureReview(Map<String, Object> review) {
        List<String> lines = new ArrayList<String>();
        lines.add("Nexus will use these approved CAD dependencies and relationship changes:");
        List<Object> dependencies = MiniJson.array(review.get("dependencies"));
        if (!dependencies.isEmpty()) {
            lines.add("");
            lines.add("Unchanged dependencies (pinned at approval):");
            for (Object value : dependencies) {
                Map<String, Object> dependency = MiniJson.object(value);
                String hash = MiniJson.text(dependency, "sha256");
                String line = "  " + MiniJson.text(dependency, "file_name") + "  Rev "
                    + MiniJson.text(dependency, "revision") + "."
                    + MiniJson.integer(dependency, "iteration");
                lines.add(line);
                if (hash.length() > 0) lines.add("    SHA-256: " + hash);
            }
        }
        List<Object> changes = MiniJson.array(review.get("changes"));
        lines.add("");
        lines.add("Assembly relationship changes:");
        if (changes.isEmpty()) {
            lines.add("  No assembly link changes");
        } else {
            for (Object value : changes) {
                Map<String, Object> change = MiniJson.object(value);
                lines.add("  " + MiniJson.text(change, "action") + "  "
                    + MiniJson.text(change, "parent_file_name") + " -> "
                    + MiniJson.text(change, "child_file_name") + "  ("
                    + MiniJson.integer(change, "before_quantity") + " to "
                    + MiniJson.integer(change, "after_quantity") + ")");
            }
        }
        List<Object> drawings = MiniJson.array(review.get("drawings"));
        if (!drawings.isEmpty()) {
            lines.add("");
            lines.add("Drawing references:");
            for (Object value : drawings) {
                Map<String, Object> relation = MiniJson.object(value);
                String owner = MiniJson.text(relation, "current_owner_file_name");
                if (owner.length() == 0) owner = "unbound";
                lines.add("  " + MiniJson.text(relation, "drawing_file_name") + " -> "
                    + MiniJson.text(relation, "model_file_name") + "  Rev "
                    + MiniJson.text(relation, "model_revision") + "."
                    + MiniJson.integer(relation, "model_iteration") + "  (current owner: "
                    + owner + ")");
            }
        }
        lines.add("");
        lines.add("CAD relationships will be published only after this Pending commit is approved.");
        return NexusDialogs.confirmScrollable(
            joinList(lines, "\n"), "Review Creo Structure", JOptionPane.QUESTION_MESSAGE
        );
    }

    private static Map<String, Object> filterCreoStructure(
        Map<String, Object> metadata, List<CheckinChoice> candidates,
        Map<Integer, CheckinChoice> selectedChoices
    ) {
        Set<String> candidateNames = new LinkedHashSet<String>();
        Set<String> selectedNames = new LinkedHashSet<String>();
        for (CheckinChoice choice : candidates) {
            String name = logicalCreoFileName(choice.fileName()).toLowerCase();
            candidateNames.add(name);
            if (selectedChoices.containsValue(choice)) selectedNames.add(name);
        }
        for (CheckinChoice choice : selectedChoices.values()) {
            selectedNames.add(logicalCreoFileName(choice.fileName()).toLowerCase());
        }
        Map<String, Object> source = MiniJson.object(metadata.get("structure"));
        Map<String, Object> filtered = new LinkedHashMap<String, Object>();
        filtered.put("schema", Integer.valueOf(1));
        List<Object> members = new ArrayList<Object>();
        for (Object value : MiniJson.array(source.get("members"))) {
            Map<String, Object> edge = MiniJson.object(value);
            String parent = MiniJson.text(edge, "parent_file_name").toLowerCase();
            String child = MiniJson.text(edge, "child_file_name").toLowerCase();
            if ((candidateNames.contains(parent) && !selectedNames.contains(parent))
                || (candidateNames.contains(child) && !selectedNames.contains(child))) continue;
            members.add(edge);
        }
        List<Object> drawings = new ArrayList<Object>();
        for (Object value : MiniJson.array(source.get("drawings"))) {
            Map<String, Object> relation = MiniJson.object(value);
            String drawing = MiniJson.text(relation, "drawing_file_name").toLowerCase();
            String model = MiniJson.text(relation, "model_file_name").toLowerCase();
            if ((candidateNames.contains(drawing) && !selectedNames.contains(drawing))
                || (candidateNames.contains(model) && !selectedNames.contains(model))) continue;
            drawings.add(relation);
        }
        filtered.put("members", members);
        filtered.put("drawings", drawings);
        List<Object> completeAssemblies = new ArrayList<Object>();
        for (Object value : MiniJson.array(source.get("complete_assemblies"))) {
            String name = String.valueOf(value).toLowerCase();
            if (selectedNames.contains(name)) completeAssemblies.add(value);
        }
        filtered.put("complete_assemblies", completeAssemblies);
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("structure", filtered);
        return result;
    }

    private static boolean loadedModelMatchesWorkspaceFile(
            Model model, String path) {

        if (model == null || path == null
                || path.trim().length() == 0) return false;

        try {
            String originText = model.GetOrigin();
            if (originText == null
                    || originText.trim().length() == 0) return false;

            File origin = new File(originText).getCanonicalFile();
            File expected = new File(path).getCanonicalFile();

            return origin.equals(expected);
        } catch (Exception error) {
            return false;
        }
    }

    private static boolean modelInSelectedWorkspaceDirectory(
            Model model, Map<String, String> selectedPaths) {

        if (model == null || selectedPaths == null
                || selectedPaths.isEmpty()) return false;

        try {
            String originText = model.GetOrigin();
            if (originText == null
                    || originText.trim().length() == 0) return false;

            File originDirectory =
                new File(originText).getCanonicalFile().getParentFile();

            if (originDirectory == null) return false;

            for (String path : selectedPaths.values()) {
                if (path == null || path.trim().length() == 0) continue;

                File expectedDirectory =
                    new File(path).getCanonicalFile().getParentFile();

                if (originDirectory.equals(expectedDirectory)) return true;
            }
        } catch (Exception error) {
            return false;
        }

        return false;
    }

    private static Map<String, Object> loadWorkspaceState(String workspaceId)
        throws Exception {
        try {
            return api.listWorkspaceState(workspaceId);
        } catch (NexusApiException error) {
            if ("route_not_found".equals(error.getCode())) {
                throw new IllegalStateException(
                    "The running Nexus bridge does not include workspace status yet.\n\n"
                        + "Close and restart the Nexus desktop app, then reconnect Creo to the project."
                );
            }
            throw error;
        }
    }

    private static Map<String, LoadedModelInfo> loadedCreoModels() {
        Map<String, LoadedModelInfo> loaded = new LinkedHashMap<String, LoadedModelInfo>();
        Model current = currentOrActiveModel();
        if (current == null) {
            return loaded;
        }
        walkLoadedModels(loaded, current, "current", new LinkedHashSet<String>());
        return loaded;
    }

    private static void walkLoadedModels(
        Map<String, LoadedModelInfo> loaded,
        Model model,
        String source,
        Set<String> visiting
    ) {
        if (model == null) return;
        String key = "";
        try { key = logicalModelFileName(model).toLowerCase(); }
        catch (Throwable ignored) { }
        if (key.length() == 0 || !visiting.add(key)) return;
        addLoadedModel(loaded, model, source);
        try {
            Dependencies dependencies = model.ListDependencies();
            if (dependencies != null) {
                for (int index = 0; index < dependencies.getarraysize(); index++) {
                    Dependency dependency = dependencies.get(index);
                    if (dependency == null || dependency.GetDepModel() == null) continue;
                    Model child = session.GetModelFromDescr(dependency.GetDepModel());
                    if (child != null) {
                        walkLoadedModels(loaded, child, "loaded child", visiting);
                    }
                }
            }
        } catch (Throwable ignored) {
        } finally {
            visiting.remove(key);
        }
    }

    private static void addLoadedModel(
        Map<String, LoadedModelInfo> loaded,
        Model model,
        String source
    ) {
        try {
            String key = logicalModelFileName(model).toLowerCase();
            if (key.length() > 0 && !loaded.containsKey(key)) {
                loaded.put(key, new LoadedModelInfo(model, source));
            }
        } catch (Throwable ignored) {
        }
    }

    private static void undoCheckout() throws Exception {
        Model current = requireCurrentModel();
        Map<String, Object> status = resolveCadForModel(current);
        requireManaged(status);
        if (!MiniJson.bool(status, "can_modify")) {
            throw new IllegalStateException("The current user does not own this CAD checkout.");
        }
        boolean confirmed = NexusDialogs.confirm(
            "Undo the Nexus checkout? Unsaved in-session changes will be discarded.\n"
                + "Local files will be retained read-only.",
            "Nexus Undo Checkout",
            JOptionPane.WARNING_MESSAGE
        );
        if (!confirmed) return;
        api.undoCheckout(MiniJson.integer(status, "id"), "Undo checkout from Creo");
        try {
            current.EraseWithDependencies();
        } catch (Throwable ignored) {
            try { current.Erase(); } catch (Throwable ignoredAgain) { }
        }
        NexusDialogs.info(
            "Checkout was undone. The retained workspace files are read-only.",
            "Nexus Undo Checkout"
        );
    }

    private static Map<String, Object> resolveCurrentModel() throws Exception {
        installCommandGuards();
        Model model = requireCurrentModel();
        Map<String, Object> status = resolveCadForModel(model);
        requireManaged(status);
        return status;
    }

    private static Model requireCurrentModel() throws Exception {
        Model model = currentOrActiveModel();
        if (model == null) {
            throw new IllegalStateException("Open or retrieve a Creo model first.");
        }
        return model;
    }

    private static Model currentOrActiveModel() {
        try {
            Model current = session.GetCurrentModel();
            if (current != null) return current;
        } catch (Throwable ignored) {
        }
        try {
            return session.GetActiveModel();
        } catch (Throwable ignored) {
            return null;
        }
    }

    private static void eraseForReload(Model model) {
        try {
            model.EraseWithDependencies();
        } catch (Throwable ignored) {
            try { model.Erase(); } catch (Throwable ignoredAgain) { }
        }
    }

    private static void ensureReloadIsSafe(
        Model root,
        Map<String, Object> workspace,
        boolean preserveLocalChanges
    ) throws Exception {
        Set<String> modified = new LinkedHashSet<String>();
        Map<String, Object> workspaceState = null;
        try {
            workspaceState = loadWorkspaceState(MiniJson.text(workspace, "id"));
        } catch (Exception ignored) {
            // Fall back to Creo's in-memory flag if the bridge cannot be queried.
        }
        if (!preserveLocalChanges && modelNeedsReloadProtection(root, workspaceState)) {
            modified.add(root.GetFileName());
        }
        for (LoadedModelInfo loaded : loadedCreoModels().values()) {
            if (loaded.model != root
                && modelNeedsReloadProtection(loaded.model, workspaceState)) {
                modified.add(loaded.model.GetFileName());
            }
        }
        if (!modified.isEmpty()) {
            throw new IllegalStateException(
                "Save or discard these modified models before checkout reloads the assembly:\n"
                    + join(modified, "\n")
            );
        }
    }

    private static boolean modelNeedsReloadProtection(
        Model model,
        Map<String, Object> workspaceState
    ) {
        if (model == null) return false;
        if (workspaceState == null) {
            try { return model.GetIsModified(); }
            catch (Throwable ignored) { return false; }
        }
        String logical = logicalModelFileName(model);
        for (Object raw : MiniJson.array(workspaceState.get("local_files"))) {
            Map<String, Object> file = MiniJson.object(raw);
            String candidate = MiniJson.text(file, "logical_file_name");
            if (logical.equalsIgnoreCase(candidate)) {
                // Creo can report a model dirty after loading dependencies even
                // when the workspace bytes still match the Nexus baseline.
                return MiniJson.bool(file, "modified");
            }
        }
        try { return model.GetIsModified(); }
        catch (Throwable ignored) { return false; }
    }

    private static String safeFileName(Model model) {
        try { return model == null ? "" : model.GetFileName(); }
        catch (Throwable ignored) { return ""; }
    }

    private static int jsonInteger(Object value) {
        if (value instanceof Number) return ((Number) value).intValue();
        String text = value == null ? "" : String.valueOf(value).trim();
        if (text.length() == 0 || "null".equalsIgnoreCase(text)) return 0;
        return Integer.parseInt(text);
    }

    private static String join(Set<String> values, String separator) {
        StringBuilder result = new StringBuilder();
        for (String value : values) {
            if (result.length() > 0) result.append(separator);
            result.append(value);
        }
        return result.toString();
    }

    private static String joinList(List<String> values, String separator) {
        StringBuilder result = new StringBuilder();
        for (String value : values) {
            if (result.length() > 0) result.append(separator);
            result.append(value);
        }
        return result.toString();
    }

    private static void requireManaged(Map<String, Object> status) {
        if (!MiniJson.bool(status, "managed")) {
            throw new IllegalStateException(
                "The current Creo file is not registered as a CAD Document in the active Nexus project."
            );
        }
    }

    private static Model displayManagedResult(Map<String, Object> result) throws Exception {
        Map<String, Object> workspace = MiniJson.object(result.get("workspace"));
        String workspacePath = MiniJson.text(workspace, "path");
        String rootPath = MiniJson.text(result, "root_path");
        if (workspacePath.length() == 0 || rootPath.length() == 0) {
            throw new IllegalStateException("Nexus did not return a managed Creo file path.");
        }
        String previousWorkspaceId = selectedWorkspace == null
            ? ""
            : MiniJson.text(selectedWorkspace, "id");
        selectedWorkspace = workspace;
        if (!previousWorkspaceId.equalsIgnoreCase(MiniJson.text(workspace, "id"))) {
            localDraftModels.clear();
        }
        session.ChangeDirectory(workspacePath);
        String logicalFileName = logicalCreoFileName(new File(rootPath).getName());
        ModelDescriptor descriptor = pfcModel.ModelDescriptor_CreateFromFileName(
            logicalFileName
        );
        Model model = session.RetrieveModel(descriptor);
        if (model == null) {
            throw new IllegalStateException("Creo could not retrieve " + rootPath + ".");
        }
        // Creo can expose feature and ribbon commands only after the first model
        // is retrieved. Retry registration at that point before the user edits.
        installCommandGuards();
        String origin = model.GetOrigin();
        if (origin != null && new File(origin).isAbsolute()) {
            String expectedRoot = new File(workspacePath).getCanonicalPath();
            String actualOrigin = new File(origin).getCanonicalPath();
            if (!(actualOrigin.equalsIgnoreCase(expectedRoot)
                || actualOrigin.toLowerCase().startsWith(
                    (expectedRoot + File.separator).toLowerCase()
                ))) {
                throw new IllegalStateException(
                    "Creo retained another in-session copy of " + model.GetFileName()
                        + ". Erase that model, then retrieve it again from Nexus."
                );
            }
        }
        Map<String, Object> status = MiniJson.object(result.get("cad"));
        if (MiniJson.bool(status, "local_edit_intent")) {
            markLocalDraft(model);
        } else {
            localDraftModels.remove(localDraftKey(model));
        }
        activateModel(model);
        return model;
    }

    private static boolean modelMatchesWorkspace(
        Model model,
        String workspacePath,
        String logicalFileName
    ) {
        if (model == null || !modelIsInsideWorkspace(model, workspacePath)) {
            return false;
        }
        return logicalModelFileName(model).equalsIgnoreCase(
            logicalCreoFileName(logicalFileName)
        );
    }

    private static boolean modelIsInsideWorkspace(Model model, String workspacePath) {
        if (model == null || workspacePath == null || workspacePath.trim().length() == 0) {
            return false;
        }
        try {
            String origin = model.GetOrigin();
            if (origin == null || origin.trim().length() == 0) return false;
            String root = new File(workspacePath).getCanonicalPath();
            String candidate = new File(origin).getCanonicalPath();
            return candidate.equalsIgnoreCase(root)
                || candidate.toLowerCase().startsWith((root + File.separator).toLowerCase());
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static void activateModel(Model model) throws Exception {
        if (model == null) {
            throw new IllegalStateException("Creo returned an empty model.");
        }
        model.Display();
        Window window = session.GetModelWindow(model);
        if (window == null) {
            window = session.CreateModelWindow(model);
        }
        if (window != null) {
            window.Activate();
            session.SetCurrentWindow(window);
            window.Refresh();
        }
        session.FlushCurrentWindow();
    }

    private static String logicalCreoFileName(String fileName) {
        String name = fileName == null ? "" : new File(fileName).getName();
        name = name.replaceFirst("(?i)\\.(prt|asm|drw)\\.\\d+$", ".$1");
        return name.replaceFirst(
            "(?i)^([^<>]+)<[^<>]+>(\\.(?:prt|asm|drw))$", "$1$2"
        );
    }

    private static String logicalModelFileName(Model model) {
        if (model == null) return "";
        String rawFileName;
        try {
            rawFileName = model.GetFileName();
        } catch (Throwable ignored) {
            return "";
        }
        String instanceFileName = logicalCreoFileName(rawFileName);
        String genericFileName = genericFamilyFileName(model, rawFileName, instanceFileName);
        if (genericFileName.length() == 0) return instanceFileName;

        Map<String, Object> workspace = selectedWorkspace;
        String workspaceId = MiniJson.text(workspace, "id");
        if (workspaceId.length() == 0 || api == null) return instanceFileName;
        try {
            if (MiniJson.bool(api.resolveCad(instanceFileName, workspaceId), "managed")) {
                return instanceFileName;
            }
            Map<String, Object> state = loadWorkspaceState(workspaceId);
            if (findUnmappedWorkspaceFile(
                MiniJson.array(state.get("local_files")), instanceFileName
            ) != null) return instanceFileName;
            if (MiniJson.bool(api.resolveCad(genericFileName, workspaceId), "managed")) {
                return genericFileName;
            }
            if (findUnmappedWorkspaceFile(
                MiniJson.array(state.get("local_files")), genericFileName
            ) != null) return genericFileName;
        } catch (Exception ignored) {
        }
        return instanceFileName;
    }

    private static String genericFamilyFileName(Model model, String rawFileName,
                                                  String instanceFileName) {
        String genericName = "";
        try {
            String value = model.GetGenericName();
            genericName = value == null ? "" : value.trim();
        } catch (Throwable ignored) {
        }
        int genericStart = rawFileName == null ? -1 : rawFileName.lastIndexOf('<');
        int genericEnd = rawFileName == null ? -1 : rawFileName.lastIndexOf('>');
        if (genericName.length() == 0 && genericStart >= 0 && genericEnd > genericStart) {
            genericName = rawFileName.substring(genericStart + 1, genericEnd).trim();
        }
        if (genericName.length() == 0) return "";

        String extension = instanceFileName.replaceFirst(
            "^.*(\\.(?:prt|asm|drw))$", "$1"
        );
        if (extension.equals(instanceFileName)) return instanceFileName;
        String genericFileName = genericName;
        if (!genericFileName.toLowerCase().endsWith(extension.toLowerCase())) {
            genericFileName += extension;
        }
        return logicalCreoFileName(genericFileName);
    }

    private static boolean isFamilyTableInstance(Model model) {
        if (model == null) return false;
        try {
            String raw = model.GetFileName();
            return genericFamilyFileName(
                model, raw, logicalCreoFileName(raw)
            ).length() > 0;
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static boolean hasSeparateFamilyInstanceIdentity(
        Model model, String selectedName
    ) {
        if (model == null) return false;
        try {
            String raw = model.GetFileName();
            return logicalCreoFileName(raw).equalsIgnoreCase(selectedName);
        } catch (Throwable ignored) {
            return false;
        }
    }

    private static String localDraftKey(Model model) {
        if (model == null || selectedWorkspace == null) return "";
        String workspaceId = MiniJson.text(selectedWorkspace, "id").toLowerCase();
        String fileName = logicalModelFileName(model).toLowerCase();
        if (workspaceId.length() == 0 || fileName.length() == 0) return "";
        return workspaceId + "|" + fileName;
    }

    static boolean isLocalDraftModel(Model model) {
        String key = localDraftKey(model);
        if (key.length() == 0 || !localDraftModels.contains(key)) return false;
        return modelMatchesWorkspace(
            model,
            MiniJson.text(selectedWorkspace, "path"),
            safeFileName(model)
        );
    }

    private static void markLocalDraft(Model model) {
        String key = localDraftKey(model);
        if (key.length() > 0) localDraftModels.add(key);
    }

    private static void showError(Throwable error) {
        String message = error == null ? "Unknown error" : error.getMessage();
        if (message == null || message.trim().length() == 0) {
            message = String.valueOf(error);
        }
        if (error instanceof NexusApiException) {
            String code = ((NexusApiException) error).getCode();
            if (code.length() > 0) message = message + "\n\nCode: " + code;
        }
        NexusDialogs.error(message, "Nexus PDM");
    }

    private interface CommandAction {
        void run() throws Exception;
    }

    private static class CommandGuardRegistration {
        final UICommand command;
        final UICommandBracketListener listener;

        CommandGuardRegistration(UICommand command, UICommandBracketListener listener) {
            this.command = command;
            this.listener = listener;
        }
    }

    private static final class CommandListener extends DefaultUICommandActionListener {
        private final CommandAction action;

        private CommandListener(CommandAction action) {
            this.action = action;
        }

        public void OnCommand() {
            try {
                action.run();
            } catch (Throwable error) {
                showError(error);
            }
        }
    }

    private static final class WorkspaceChoice {
        private final Map<String, Object> workspace;

        private WorkspaceChoice(Map<String, Object> workspace) {
            this.workspace = workspace;
        }

        public String toString() {
            if (workspace == null) return "<Create new workspace...>";
            String name = MiniJson.text(workspace, "name");
            String path = MiniJson.text(workspace, "path");
            return name + (path.length() == 0 ? "" : "  [" + path + "]");
        }
    }

    private static final class LoadedModelInfo {
        private final Model model;
        private final String source;

        private LoadedModelInfo(Model model, String source) {
            this.model = model;
            this.source = source;
        }

        private boolean modified() {
            try {
                return model.GetIsModified();
            } catch (Throwable ignored) {
                return false;
            }
        }
    }

    private static final class CheckinChoice {
        private Map<String, Object> status;
        private Map<String, Object> localFile;
        private boolean workspaceCheckout;
        private LoadedModelInfo loaded;

        private CheckinChoice(Map<String, Object> status) {
            this.status = status;
        }

        private String fileName() {
            String name = MiniJson.text(status, "file_name");
            if (name.length() == 0 && localFile != null) {
                name = MiniJson.text(localFile, "logical_file_name");
            }
            if (name.length() == 0 && localFile != null) {
                name = MiniJson.text(localFile, "filename");
            }
            return name;
        }

        private boolean defaultSelected() {
            if (isNewCadCandidate()) return false;
            return MiniJson.bool(status, "can_checkin")
                && (
                    (loaded != null && ("current".equals(loaded.source) || loaded.modified()))
                    || (localFile != null && MiniJson.bool(localFile, "selectable"))
                );
        }

        private boolean modified() {
            if (isNewCadCandidate()) return true;
            if (loaded != null && loaded.modified()) return true;
            if (localFile == null) return false;
            if (MiniJson.bool(localFile, "modified")) return true;
            String localStatus = MiniJson.text(localFile, "status").toLowerCase();
            return localStatus.indexOf("modified") >= 0
                || localStatus.indexOf("changed") >= 0;
        }

        private NexusDialogs.ChecklistItem checklistItem() {
            String name = fileName();
            if (name.length() == 0) name = MiniJson.text(status, "number");
            boolean drawing = isDrawingChoice(this);
            String revision = MiniJson.text(status, "revision");
            int iteration = MiniJson.integer(status, "iteration");
            String primary = drawing ? name + "    [DRAWING]" : name;
            if (revision.length() > 0 || iteration > 0) {
                primary += "    Rev " + revision + "." + iteration;
            }

            boolean newCandidate = isNewCadCandidate();
            boolean selectable = newCandidate || MiniJson.bool(status, "can_checkin");
            boolean changed = modified();
            String state = !selectable
                ? NexusDialogs.ChecklistItem.BLOCKED
                : changed
                    ? NexusDialogs.ChecklistItem.MODIFIED
                    : NexusDialogs.ChecklistItem.READY;
            List<String> context = new ArrayList<String>();
            if (loaded != null) {
                context.add("current".equals(loaded.source) ? "Current model" : "Loaded child");
            } else {
                context.add("Not loaded");
            }
            if (workspaceCheckout) context.add("Workspace checkout");
            else if (localFile != null) context.add("Workspace file");
            if (drawing) context.add("Creo drawing");
            if (newCandidate) context.add("New CAD Document");

            String reason = MiniJson.text(status, "read_only_reason");
            if (reason.length() == 0 && localFile != null) {
                reason = MiniJson.text(localFile, "detail");
            }
            String description = !selectable
                ? reason.length() > 0 ? reason : "This CAD Document cannot be checked in."
                : newCandidate
                    ? drawing
                        ? "New native Creo drawing. Nexus will register and check it out before staging."
                        : "New native Creo model. Nexus will register and check it out before staging."
                : drawing
                    ? "Creo drawing ready to check in. Nexus will include its related model when required."
                : changed
                    ? "The CAD Document has local modifications and is ready to check in."
                    : "The checked-out CAD Document is ready to check in.";
            String visibleDescription = description.length() > 92
                ? description.substring(0, 89) + "..."
                : description;
            return new NexusDialogs.ChecklistItem(
                primary,
                visibleDescription,
                state,
                selectable,
                description,
                joinList(context, " | ")
            );
        }

        public String toString() {
            String label = fileName();
            if (label.length() == 0) {
                label = MiniJson.text(status, "number");
            }
            String revision = MiniJson.text(status, "revision");
            int iteration = MiniJson.integer(status, "iteration");
            if (revision.length() > 0 || iteration > 0) {
                label = label + "  Rev " + revision + "." + iteration;
            }
            List<String> tags = new ArrayList<String>();
            if (localFile != null) tags.add("local workspace");
            if (workspaceCheckout) tags.add("workspace checkout");
            if (loaded != null) {
                tags.add(loaded.source);
                if (loaded.modified()) tags.add("modified");
            } else {
                tags.add("not loaded");
            }
            if (localFile != null) {
                String statusText = MiniJson.text(localFile, "status");
                if (statusText.length() > 0) tags.add(statusText);
            }
            if (!MiniJson.bool(status, "can_checkin")) tags.add("read-only");
            label = label + "  [" + joinList(tags, ", ") + "]";
            String reason = MiniJson.text(status, "read_only_reason");
            if (reason.length() == 0 && localFile != null) {
                reason = MiniJson.text(localFile, "detail");
            }
            if (!MiniJson.bool(status, "can_checkin") && reason.length() > 0) {
                label = label + " - " + reason;
            }
            return label;
        }

        private boolean isNewCadCandidate() {
            return MiniJson.bool(status, "new_candidate");
        }
    }
}
