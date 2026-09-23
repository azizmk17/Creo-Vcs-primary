import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcFeature.DefaultFeatureActionListener;
import com.ptc.pfc.pfcFeature.Feature;
import com.ptc.pfc.pfcFeature.FeatureCopyType;
import com.ptc.pfc.pfcModelItem.Parameter;
import com.ptc.pfc.pfcModelItem.ParamValue;

/** Guards feature edits for parts and component-placement features in assemblies. */
public class NexusFeatureMutationGuard extends DefaultFeatureActionListener {
    private void require(Feature feature, String operation) throws jxthrowable {
        NexusJLink.requireModelEdit(NexusJLink.modelForChild(feature), operation);
    }

    public void OnBeforeDelete(Feature feature) throws jxthrowable {
        require(feature, "feature deletion");
    }

    public void OnBeforeSuppress(Feature feature) throws jxthrowable {
        require(feature, "feature suppression");
    }

    public void OnBeforeRedefine(Feature feature) throws jxthrowable {
        require(feature, "feature or assembly-constraint redefinition");
    }

    public void OnBeforeParameterCreate(
        Feature owner, String name, ParamValue value
    ) throws jxthrowable {
        require(owner, "feature parameter creation");
    }

    public void OnBeforeParameterModify(Parameter parameter, ParamValue value)
        throws jxthrowable {
        NexusJLink.requireModelEdit(
            NexusJLink.modelForChild(parameter), "feature parameter modification"
        );
    }

    public void OnBeforeParameterDelete(Parameter parameter) throws jxthrowable {
        NexusJLink.requireModelEdit(
            NexusJLink.modelForChild(parameter), "feature parameter deletion"
        );
    }

    public void OnAfterSuppress(Feature feature) throws jxthrowable {
        NexusJLink.refreshEditProtection();
    }

    public void OnAfterCopy(
        Feature source, Feature target, FeatureCopyType type
    ) throws jxthrowable {
        NexusJLink.refreshEditProtection();
    }
}
