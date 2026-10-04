import com.ptc.pfc.pfcModelItem.Parameter;
import com.ptc.pfc.pfcModelItem.ParamValue;
import com.ptc.pfc.pfcModelItem.ParamValueType;

import java.io.File;
import java.io.FileOutputStream;
import java.io.OutputStreamWriter;
import java.io.PrintWriter;
import java.util.Date;

/**
 * Candidate correction and diagnostic for Nexus parameter-change callbacks.
 * Java 7 syntax. Compile with the J-Link libraries from the target Creo install.
 * Not compiled against pfc.jar or executed inside Creo by the author.
 *
 * Put this file beside NexusModelMutationGuard.java. In that class, replace ONLY
 * OnBeforeParameterModify with:
 *
 * public void OnBeforeParameterModify(Parameter parameter, ParamValue value)
 *         throws com.ptc.cipjava.jxthrowable {
 *     if (NexusParameterChangeGuard.isUnchanged(parameter, value)) return;
 *     NexusJLink.requireModelEdit(
 *         NexusJLink.modelForChild(parameter), "parameter modification");
 * }
 *
 * Include this source in your existing compilation and its resulting class in
 * the deployed JAR/classes directory, then restart Creo to load the change.
 * Keep all other guards and checkout/save rules.
 *
 * Logs each invocation to java.io.tmpdir/nexus-parameter-trace.log (normally
 * %TEMP% on Windows), including the absolute log path in every record.
 * If the popup remains, inspect GUARD_CHANGED_OR_UNKNOWN records to identify
 * the parameter and old/proposed values. Relation-driven status is information
 * only: it NEVER grants permission. There is no name/prefix bypass.
 *
 * Check with an unchecked-out disposable part: open it, regenerate it, then
 * deliberately change a user parameter. A different value must still reach
 * the existing checkout/conflict guard. This candidate addresses identical
 * value assignments only; it cannot settle the cause of genuine auto-updates.
 */
public final class NexusParameterChangeGuard {
    private NexusParameterChangeGuard() { }

    /** True only for a readable, exactly equal value of a supported type. */
    public static boolean isUnchanged(Parameter parameter, ParamValue proposed) {
        String name = "<unavailable>";
        String relationDriven = "<unavailable>";
        String comparisonError = "";
        ParamValue current = null;
        boolean identical = false;

        try {
            if (parameter != null) name = parameter.GetName();
        } catch (Throwable error) {
            rethrowFatal(error);
        }
        try {
            if (parameter != null) {
                current = parameter.GetValue();
                identical = sameExactValue(current, proposed);
            }
        } catch (Throwable error) {
            rethrowFatal(error);
            comparisonError = error.toString();
            identical = false;
        }
        try {
            if (parameter != null) {
                relationDriven = String.valueOf(parameter.GetIsRelationDriven());
            }
        } catch (Throwable error) {
            rethrowFatal(error);
        }

        appendTrace("parameter=" + escape(name)
            + " relationDriven=" + relationDriven
            + " current=" + describe(current)
            + " proposed=" + describe(proposed)
            + " decision=" + (identical
                ? "SKIP_IDENTICAL_VALUE" : "GUARD_CHANGED_OR_UNKNOWN")
            + " comparisonError=" + escape(comparisonError));
        return identical;
    }

    private static boolean sameExactValue(ParamValue a, ParamValue b)
            throws com.ptc.cipjava.jxthrowable {
        if (a == null || b == null || a.Getdiscr() == null || b.Getdiscr() == null) {
            return false;
        }
        int type = a.Getdiscr().getValue();
        if (type != b.Getdiscr().getValue()) return false;

        if (type == ParamValueType._PARAM_STRING) {
            String x = a.GetStringValue();
            String y = b.GetStringValue();
            return x != null && y != null && x.equals(y);
        }
        if (type == ParamValueType._PARAM_INTEGER) {
            return a.GetIntValue() == b.GetIntValue();
        }
        if (type == ParamValueType._PARAM_BOOLEAN) {
            return a.GetBoolValue() == b.GetBoolValue();
        }
        if (type == ParamValueType._PARAM_DOUBLE) {
            double x = a.GetDoubleValue();
            double y = b.GetDoubleValue();
            if (Double.isNaN(x) || Double.isNaN(y)
                    || Double.isInfinite(x) || Double.isInfinite(y)) return false;
            // No tolerance: even very small changes still require authorization.
            return Double.doubleToRawLongBits(x) == Double.doubleToRawLongBits(y);
        }
        // Notes, unset values and other unsupported types keep the existing guard.
        return false;
    }

    private static String describe(ParamValue value) {
        if (value == null) return "<null>";
        try {
            if (value.Getdiscr() == null) return "<unknown-type>";
            int type = value.Getdiscr().getValue();
            if (type == ParamValueType._PARAM_STRING) {
                return "STRING:\"" + escape(value.GetStringValue()) + "\"";
            }
            if (type == ParamValueType._PARAM_INTEGER) {
                return "INTEGER:" + value.GetIntValue();
            }
            if (type == ParamValueType._PARAM_BOOLEAN) {
                return "BOOLEAN:" + value.GetBoolValue();
            }
            if (type == ParamValueType._PARAM_DOUBLE) {
                double number = value.GetDoubleValue();
                return "DOUBLE:" + number + "[" + Double.toHexString(number) + "]";
            }
            if (type == ParamValueType._PARAM_NOTE) {
                return "NOTE_ID:" + value.GetNoteId();
            }
            return "UNSUPPORTED_TYPE:" + type;
        } catch (Throwable error) {
            rethrowFatal(error);
            return "<unreadable:" + escape(error.toString()) + ">";
        }
    }

    private static void appendTrace(String text) {
        try {
            File path = new File(System.getProperty("java.io.tmpdir"),
                "nexus-parameter-trace.log");
            synchronized (NexusParameterChangeGuard.class) {
                try (PrintWriter out = new PrintWriter(new OutputStreamWriter(
                        new FileOutputStream(path, true), "UTF-8"))) {
                    out.println(new Date() + " " + text);
                    out.println("log=" + path.getAbsolutePath());
                    if (out.checkError()) {
                        System.err.println("Nexus parameter trace write failed: " + path);
                    }
                }
            }
        } catch (Exception error) {
            // A logging failure never grants permission or replaces the edit guard.
            System.err.println("Nexus parameter trace unavailable: " + error);
        }
    }

    private static String escape(String value) {
        if (value == null) return "<null>";
        return value.replace("\\", "\\\\").replace("\r", "\\r")
            .replace("\n", "\\n").replace("\t", "\\t").replace("\"", "\\\"");
    }

    private static void rethrowFatal(Throwable error) {
        if (error instanceof ThreadDeath) throw (ThreadDeath) error;
        if (error instanceof VirtualMachineError) throw (VirtualMachineError) error;
    }
}
