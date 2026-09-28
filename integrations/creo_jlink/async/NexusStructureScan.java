import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStreamWriter;
import java.io.Reader;
import java.io.Writer;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;

import com.ptc.pfc.pfcAsyncConnection.AsyncConnection;
import com.ptc.pfc.pfcAsyncConnection.pfcAsyncConnection;
import com.ptc.pfc.pfcComponentFeat.ComponentFeat;
import com.ptc.pfc.pfcFeature.Feature;
import com.ptc.pfc.pfcFeature.Features;
import com.ptc.pfc.pfcFeature.FeatureStatus;
import com.ptc.pfc.pfcFeature.FeatureType;
import com.ptc.pfc.pfcExceptions.XToolkitError;
import com.ptc.pfc.pfcModel.Dependencies;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcModel.ModelDescriptor;
import com.ptc.pfc.pfcModel.Models;
import com.ptc.pfc.pfcModel.pfcModel;
import com.ptc.pfc.pfcModel2D.Model2D;
import com.ptc.pfc.pfcSession.Session;
import com.ptc.pfc.pfcSolid.Solid;

/** Read-only, one-job process. Never attaches to the designer's Creo session. */
public final class NexusStructureScan {
    private final Map<String, Object> documents = new LinkedHashMap<String, Object>();
    private final List<String> errors = new ArrayList<String>();
    private Map<String, Object> files;
    private File directory;
    private Session session;

    private static String describe(Throwable failure) {
        StringBuilder message = new StringBuilder(failure.toString());
        if (failure instanceof XToolkitError) {
            XToolkitError toolkit = (XToolkitError) failure;
            try { message.append("; Creo error code=").append(toolkit.GetErrorCode()); }
            catch (Throwable ignored) { }
            try { message.append("; Toolkit function=").append(toolkit.GetToolkitFunctionName()); }
            catch (Throwable ignored) { }
            try { message.append("; PTC message=").append(toolkit.GetMessage()); }
            catch (Throwable ignored) { }
            try { message.append("; J-Link method=").append(toolkit.GetMethodName()); }
            catch (Throwable ignored) { }
        }
        return message.toString();
    }

    private static String logical(String value) {
        return new File(value).getName().toLowerCase(Locale.ENGLISH).replaceFirst("\\.\\d+$", "");
    }

    private void inspect(String name, int depth) {
        if (documents.containsKey(name)) return;
        String stage = "initialization";
        Map<String, Object> row = new LinkedHashMap<String, Object>();
        documents.put(name, row);
        row.put("file_name", name);
        row.put("complete", Boolean.FALSE);
        List<Object> occurrences = new ArrayList<Object>();
        List<String> refs = new ArrayList<String>();
        List<String> dependencies = new ArrayList<String>();
        row.put("occurrences", occurrences);
        row.put("drawing_models", refs);
        row.put("dependencies", dependencies);
        try {
            stage = "validating dependency depth and file inventory";
            if (depth > 100) throw new IllegalArgumentException("Assembly nesting exceeds 100 levels");
            if (!files.containsKey(name)) throw new IllegalArgumentException("Missing pinned native file: " + name);
            String physical = String.valueOf(files.get(name));
            stage = "creating model descriptor";
            ModelDescriptor descriptor = pfcModel.ModelDescriptor_CreateFromFileName(name);
            stage = "setting model descriptor path";
            descriptor.SetPath(directory.getCanonicalPath());
            String suffix = physical.substring(name.length());
            if (suffix.matches("\\.\\d+")) descriptor.SetFileVersion(Integer.valueOf(suffix.substring(1)));
            stage = "retrieving model";
            Model model = session.RetrieveModel(descriptor);
            if (model == null) throw new IllegalStateException("Creo returned no model");
            stage = "validating retrieved descriptor path";
            ModelDescriptor retrievedDescriptor = model.GetDescr();
            String origin = retrievedDescriptor.GetPath();
            File originPath = new File(origin == null ? "" : origin).getCanonicalFile();
            File expected = new File(directory, physical).getCanonicalFile();
            if (!originPath.equals(directory) && !originPath.equals(expected)) {
                throw new IllegalStateException("Model descriptor does not point into the isolated scan directory: " + origin);
            }
            stage = "validating model identity";
            if (!logical(model.GetFileName()).equals(name)) throw new IllegalStateException("Retrieved identity mismatch");
            stage = "checking family table instance";
            String generic = model.GetGenericName();
            if (generic != null && generic.length() > 0) {
                throw new IllegalStateException("Family-table instance requires an explicit instance/generic policy");
            }
            stage = "reading model version";
            row.put("version_stamp", model.GetVersionStamp());
            stage = "checking model retrieval status";
            if (model instanceof Solid && ((Solid) model).HasRetrievalErrors()) {
                throw new IllegalStateException("Creo reported retrieval errors");
            }
            // Dependencies are evidence only; they are NOT assembly membership.
            stage = "listing model dependencies";
            Dependencies deps = model.ListDependencies();
            if (deps != null) for (int i = 0; i < deps.getarraysize(); i++) {
                stage = "reading dependency " + i;
                String dependency = logical(deps.get(i).GetDepModel().GetFileName());
                dependencies.add(dependency);
                if (dependency.matches(".*\\.(prt|asm|drw)")) inspect(dependency, depth + 1);
            }
            if (name.endsWith(".asm")) {
                stage = "listing assembly components";
                Solid solid = (Solid) model;
                // Include inactive features. A suppression must never look like a deletion.
                Features features = solid.ListFeaturesByType(Boolean.FALSE, FeatureType.FEATTYPE_COMPONENT);
                if (features != null) for (int i = 0; i < features.getarraysize(); i++) {
                    Feature feature = features.get(i);
                    ComponentFeat component = (ComponentFeat) feature;
                    String child = logical(component.GetModelDescr().GetFileName());
                    boolean active = feature.GetStatus().getValue() == FeatureStatus._FEAT_ACTIVE;
                    Map<String, Object> occurrence = new LinkedHashMap<String, Object>();
                    occurrence.put("feature_id", Integer.valueOf(feature.GetId()));
                    occurrence.put("child", child);
                    occurrence.put("status", active ? "ACTIVE" : "UNRESOLVED_OR_SUPPRESSED");
                    occurrences.add(occurrence);
                    if (component.GetIsSubstitute() || component.GetIsBulkitem()) {
                        throw new IllegalStateException("Substitute/bulk component requires explicit review: " + child);
                    }
                    if (!active) throw new IllegalStateException("Suppressed/inactive component: " + child);
                    inspect(child, depth + 1);
                }
            } else if (name.endsWith(".drw")) {
                stage = "listing drawing models";
                Models models = ((Model2D) model).ListModels();
                if (models != null) for (int i = 0; i < models.getarraysize(); i++) {
                    String related = logical(models.get(i).GetFileName());
                    if (!refs.contains(related)) refs.add(related);
                    inspect(related, depth + 1);
                }
            }
            row.put("complete", Boolean.TRUE);
        } catch (Throwable failure) {
            errors.add(name + " (" + stage + "): " + describe(failure));
        }
    }

    private void run(File request, File output) throws Exception {
        StringBuilder text = new StringBuilder();
        Reader reader = new InputStreamReader(new FileInputStream(request), "UTF-8");
        try {
            char[] buffer = new char[8192];
            int count;
            while ((count = reader.read(buffer)) != -1) text.append(buffer, 0, count);
        } finally { reader.close(); }
        Map<String, Object> input = MiniJson.object(MiniJson.parse(text.toString()));
        files = MiniJson.object(input.get("files"));
        directory = new File(MiniJson.text(input, "directory")).getCanonicalFile();
        AsyncConnection connection = null;
        try {
            System.loadLibrary("pfcasyncmt");
            System.err.println("PRO_DIRECTORY=" + System.getenv("PRO_DIRECTORY"));
            System.err.println("PRO_COMM_MSG_EXE=" + System.getenv("PRO_COMM_MSG_EXE"));
            System.err.println("PTCNMSPORT=" + System.getenv("PTCNMSPORT"));
            System.err.println("Starting isolated Creo: " + MiniJson.text(input, "start_command"));
            connection = pfcAsyncConnection.AsyncConnection_Start(MiniJson.text(input, "start_command"), null);
            System.err.println("Connected to isolated Creo.");
            session = connection.GetSession();
            session.ChangeDirectory(directory.getCanonicalPath());
            for (Object root : MiniJson.array(input.get("roots"))) inspect(String.valueOf(root), 0);
        } catch (Throwable failure) {
            String detail = "Creo startup/scan: " + describe(failure);
            System.err.println(detail);
            errors.add(detail);
        } finally {
            if (connection != null) try {
                System.err.println("Ending isolated Creo.");
                connection.End();
                System.err.println("Isolated Creo stopped.");
            }
            catch (Throwable failure) { errors.add("Creo shutdown: " + describe(failure)); }
        }
        Map<String, Object> result = new LinkedHashMap<String, Object>();
        result.put("schema", Integer.valueOf(1));
        result.put("complete", Boolean.valueOf(errors.isEmpty()));
        result.put("documents", new ArrayList<Object>(documents.values()));
        result.put("errors", errors);
        Writer writer = new OutputStreamWriter(new FileOutputStream(output), "UTF-8");
        try { writer.write(MiniJson.stringify(result)); } finally { writer.close(); }
        System.err.println("Native scan result written; complete=" + errors.isEmpty()
                + ", documents=" + documents.size() + ", errors=" + errors.size());
        if (!errors.isEmpty()) System.err.println("Native scan errors: " + errors);
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 2) throw new IllegalArgumentException("Expected request.json and result.json paths");
        new NexusStructureScan().run(new File(args[0]), new File(args[1]));
    }
}
