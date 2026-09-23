import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcFeature.Feature;
import com.ptc.pfc.pfcSolid.DefaultSolidActionListener;
import com.ptc.pfc.pfcSolid.Solid;

/** Guards structural mutations that create or regenerate solid features. */
public class NexusSolidMutationGuard extends DefaultSolidActionListener {
    public void OnBeforeFeatureCreate(Solid solid, int featureType)
        throws jxthrowable {
        NexusJLink.requireModelEdit(solid, "feature creation");
    }

    public void OnBeforeUnitConvert(Solid solid, boolean scale) throws jxthrowable {
        NexusJLink.requireModelEdit(solid, "unit conversion");
    }

    public void OnAfterFeatureCreate(Solid solid, Feature feature)
        throws jxthrowable {
        NexusJLink.refreshEditProtection();
    }

    public void OnAfterFeatureDelete(Solid solid, int featureId)
        throws jxthrowable {
        NexusJLink.refreshEditProtection();
    }
}
