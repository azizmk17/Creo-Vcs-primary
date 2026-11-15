import java.awt.BorderLayout;
import java.awt.Color;
import java.awt.Dimension;
import java.awt.Dialog;
import java.awt.FlowLayout;
import java.awt.Frame;
import java.awt.GridBagConstraints;
import java.awt.GridBagLayout;
import java.awt.Insets;
import java.awt.event.ActionEvent;
import java.awt.event.ActionListener;
import java.awt.event.FocusAdapter;
import java.awt.event.FocusEvent;
import java.awt.event.MouseAdapter;
import java.awt.event.MouseEvent;
import java.awt.event.WindowAdapter;
import java.awt.event.WindowEvent;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.Set;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.BlockingQueue;
import java.util.concurrent.LinkedBlockingQueue;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import javax.swing.AbstractCellEditor;
import javax.swing.BorderFactory;
import javax.swing.JButton;
import javax.swing.JCheckBox;
import javax.swing.JComboBox;
import javax.swing.JDialog;
import javax.swing.DefaultListModel;
import javax.swing.JFileChooser;
import javax.swing.JLabel;
import javax.swing.JList;
import javax.swing.JOptionPane;
import javax.swing.JPanel;
import javax.swing.JProgressBar;
import javax.swing.JScrollPane;
import javax.swing.JTable;
import javax.swing.JTextArea;
import javax.swing.JTextField;
import javax.swing.JTextPane;
import javax.swing.SwingUtilities;
import javax.swing.SwingWorker;
import javax.swing.event.DocumentEvent;
import javax.swing.event.DocumentListener;
import javax.swing.table.AbstractTableModel;
import javax.swing.table.DefaultTableCellRenderer;
import javax.swing.table.TableCellEditor;
import javax.swing.table.TableCellRenderer;
import javax.swing.table.TableColumn;
import javax.swing.text.BadLocationException;
import javax.swing.text.SimpleAttributeSet;
import javax.swing.text.StyleConstants;
import javax.swing.text.StyledDocument;

import com.ptc.cipjava.jxthrowable;
import com.ptc.pfc.pfcArgument.ArgValue;
import com.ptc.pfc.pfcArgument.pfcArgument;
import com.ptc.pfc.pfcExport.PDFExportInstructions;
import com.ptc.pfc.pfcExport.PDFOption;
import com.ptc.pfc.pfcExport.PDFOptionType;
import com.ptc.pfc.pfcExport.PDFOptions;
import com.ptc.pfc.pfcExport.PrintSheets;
import com.ptc.pfc.pfcExport.pfcExport;
import com.ptc.pfc.pfcModel.GeomExportFlags;
import com.ptc.pfc.pfcModel.Model;
import com.ptc.pfc.pfcModel.ModelType;
import com.ptc.pfc.pfcModel.Models;
import com.ptc.pfc.pfcModel.STEPExportInstructions;
import com.ptc.pfc.pfcModel.pfcModel;
import com.ptc.pfc.pfcModel2D.Model2D;
import com.ptc.pfc.pfcModelItem.ParamValue;
import com.ptc.pfc.pfcModelItem.ParamValueType;
import com.ptc.pfc.pfcModelItem.Parameter;
import com.ptc.pfc.pfcModelItem.Parameters;
import com.ptc.pfc.pfcSession.Session;
import com.ptc.pfc.pfcWindow.Window;

/** Preview-first PDF/STEP export for models already loaded in the active Creo session. */
public final class NexusDeliverableExporter {
    private static final String DEFAULT_PDF_TEMPLATE = "{NUMERO_ARTICLE}-{INDEX_AES}-{NOM}";
    private static final String DEFAULT_STEP_TEMPLATE = "{NUMERO_ARTICLE}-{INDEX_AES}";
    private static final Pattern TOKEN = Pattern.compile("\\{([^{}]+)\\}");
    private static final String[] COLUMNS = new String[] {
        "Export", "Loaded drawing", "Referenced 3D models", "Model for PDF naming"
    };

    private final Session session;
    private final List<DrawingRow> drawings = new ArrayList<DrawingRow>();
    private final Properties preferences = new Properties();
    private final ExportTableModel tableModel = new ExportTableModel();
    private final JTextField folderField = new JTextField(48);
    private final JTextField pdfTemplateField = new JTextField(DEFAULT_PDF_TEMPLATE, 32);
    private final JTextField stepTemplateField = new JTextField(DEFAULT_STEP_TEMPLATE, 32);
    private final JCheckBox pdfCheck = new JCheckBox("PDF drawings", true);
    private final JCheckBox stepCheck = new JCheckBox("STEP referenced models", true);
    private final JCheckBox uppercaseCheck = new JCheckBox("Uppercase filenames");
    private final JTextPane preview = new JTextPane();
    private JTextField activeTemplateField = pdfTemplateField;
    private final JLabel countLabel = new JLabel(" ");
    private JTable drawingTable;
    private JDialog setupDialog;
    private JPanel setupPanel;
    private JLabel loadingLabel;
    private JButton exportButton;
    private volatile boolean cancelled;
    private volatile boolean exportRequested;
    private final CountDownLatch dialogShown = new CountDownLatch(1);
    private final CountDownLatch dialogFinished = new CountDownLatch(1);
    private final BlockingQueue<Runnable> creoWork = new LinkedBlockingQueue<Runnable>();
    private static final Runnable FINISH_WORK = new Runnable() { public void run() { } };
    private int previewGeneration;

    private NexusDeliverableExporter(Session session) {
        this.session = session;
    }

    public static void open(Session session) throws Exception {
        new NexusDeliverableExporter(session).show();
    }

    private void show() throws Exception {
        SwingUtilities.invokeAndWait(new Runnable() {
            public void run() { createSetupDialog(); }
        });
        dialogShown.await();
        if (cancelled) return;

        String savedFolder;
        List<DrawingRow> loadedDrawings;
        try {
            loadPreferences();
            savedFolder = preferences.getProperty("outputFolder", "");
            if (savedFolder.length() == 0) {
                savedFolder = session.GetCurrentDirectory();
            }
            loadedDrawings = cancelled ? new ArrayList<DrawingRow>() : collectLoadedDrawings();
        } catch (final Throwable error) {
            SwingUtilities.invokeLater(new Runnable() {
                public void run() {
                    loadingLabel.setText("Could not load Creo drawings: " + String.valueOf(error));
                }
            });
            dialogFinished.await();
            return;
        }
        if (cancelled) return;
        final String outputFolder = savedFolder == null ? "" : savedFolder;
        final List<DrawingRow> loaded = loadedDrawings;
        SwingUtilities.invokeAndWait(new Runnable() {
            public void run() {
                if (cancelled) return;
                drawings.addAll(loaded);
                folderField.setText(outputFolder);
                pdfTemplateField.setText(preferences.getProperty("pdfTemplate", DEFAULT_PDF_TEMPLATE));
                stepTemplateField.setText(preferences.getProperty("stepTemplate", DEFAULT_STEP_TEMPLATE));
                pdfCheck.setSelected(Boolean.parseBoolean(preferences.getProperty("includePdf", "true")));
                stepCheck.setSelected(Boolean.parseBoolean(preferences.getProperty("includeStep", "true")));
                uppercaseCheck.setSelected(Boolean.parseBoolean(preferences.getProperty("uppercaseNames", "false")));
                setEnabledRecursively(setupPanel, true);
                tableModel.fireTableDataChanged();
                if (drawings.isEmpty()) {
                    loadingLabel.setText("No drawings are loaded. Load drawings in Creo and open this window again.");
                } else {
                    loadingLabel.setText("Select drawings to load their models and parameters.");
                    updateLoadingState();
                    refreshPreview();
                }
            }
        });
        while (!cancelled && !exportRequested) {
            Runnable work = creoWork.take();
            if (work == FINISH_WORK) break;
            work.run();
        }
        if (!exportRequested || cancelled) return;
        if (!validateSelections()) return;
        if (!savePreferences()) JOptionPane.showMessageDialog(null,
            "Could not save the naming rules. Export will continue; check access to " + preferenceFile(),
            "Nexus Deliverables", JOptionPane.WARNING_MESSAGE);
        List<ExportTask> tasks = buildPlan();
        if (tasks.isEmpty()) {
            JOptionPane.showMessageDialog(null, "There are no deliverables selected for export.",
                "Nexus Deliverables", JOptionPane.INFORMATION_MESSAGE);
            return;
        }
        execute(tasks);
    }

    private void createSetupDialog() {
        setupPanel = buildPanel();
        setEnabledRecursively(setupPanel, false);
        loadingLabel = new JLabel("Listing drawings from Creo...");
        exportButton = new JButton("Export");
        exportButton.setEnabled(false);
        exportButton.addActionListener(new ActionListener() {
            public void actionPerformed(ActionEvent event) {
                if (drawingTable != null && drawingTable.isEditing()
                    && drawingTable.getCellEditor() != null) drawingTable.getCellEditor().stopCellEditing();
                if (!exportButton.isEnabled()) return;
                exportRequested = true;
                setupDialog.dispose();
                dialogFinished.countDown();
                creoWork.offer(FINISH_WORK);
            }
        });
        JButton cancel = new JButton("Cancel");
        cancel.addActionListener(new ActionListener() {
            public void actionPerformed(ActionEvent event) { cancelSetup(); }
        });
        JPanel footer = new JPanel(new BorderLayout(8, 8));
        footer.add(loadingLabel, BorderLayout.CENTER);
        JPanel buttons = new JPanel(new FlowLayout(FlowLayout.RIGHT));
        buttons.add(cancel);
        buttons.add(exportButton);
        footer.add(buttons, BorderLayout.EAST);
        JPanel content = new JPanel(new BorderLayout(8, 8));
        content.setBorder(BorderFactory.createEmptyBorder(8, 8, 8, 8));
        content.add(setupPanel, BorderLayout.CENTER);
        content.add(footer, BorderLayout.SOUTH);
        setupDialog = new JDialog((Frame) null, "Nexus | Export Deliverables", true);
        setupDialog.setDefaultCloseOperation(JDialog.DO_NOTHING_ON_CLOSE);
        setupDialog.addWindowListener(new WindowAdapter() {
            public void windowOpened(WindowEvent event) { dialogShown.countDown(); }
            public void windowClosing(WindowEvent event) { cancelSetup(); }
        });
        setupDialog.setContentPane(content);
        setupDialog.pack();
        setupDialog.setLocationRelativeTo(null);
        SwingUtilities.invokeLater(new Runnable() {
            public void run() { setupDialog.setVisible(true); }
        });
    }

    private void cancelSetup() {
        cancelled = true;
        setupDialog.dispose();
        dialogShown.countDown();
        dialogFinished.countDown();
        creoWork.offer(FINISH_WORK);
    }

    private void setEnabledRecursively(java.awt.Component component, boolean enabled) {
        component.setEnabled(enabled);
        if (component instanceof java.awt.Container) {
            for (java.awt.Component child : ((java.awt.Container) component).getComponents()) {
                setEnabledRecursively(child, enabled);
            }
        }
    }

    private JPanel buildPanel() {
        JPanel root = new JPanel(new BorderLayout(8, 8));
        root.setPreferredSize(new Dimension(1060, 690));

        JPanel controls = new JPanel(new GridBagLayout());
        controls.setBorder(BorderFactory.createTitledBorder("Export setup"));
        GridBagConstraints c = new GridBagConstraints();
        c.insets = new Insets(3, 5, 3, 5);
        c.anchor = GridBagConstraints.WEST;
        c.gridx = 0; c.gridy = 0; controls.add(new JLabel("Output folder:"), c);
        c.gridx = 1; c.weightx = 1; c.fill = GridBagConstraints.HORIZONTAL; controls.add(folderField, c);
        JButton browse = new JButton("Browse...");
        browse.addActionListener(new ActionListener() {
            public void actionPerformed(ActionEvent event) { chooseFolder(); }
        });
        c.gridx = 2; c.weightx = 0; c.fill = GridBagConstraints.NONE; controls.add(browse, c);
        c.gridx = 0; c.gridy = 1; controls.add(new JLabel("PDF filename:"), c);
        c.gridx = 1; c.fill = GridBagConstraints.HORIZONTAL; controls.add(pdfTemplateField, c);
        c.gridx = 2; c.fill = GridBagConstraints.NONE; controls.add(new JLabel(".pdf"), c);
        c.gridx = 0; c.gridy = 2; controls.add(new JLabel("STEP filename:"), c);
        c.gridx = 1; c.fill = GridBagConstraints.HORIZONTAL; controls.add(stepTemplateField, c);
        c.gridx = 2; c.fill = GridBagConstraints.NONE; controls.add(new JLabel(".step"), c);
        JPanel options = new JPanel(new FlowLayout(FlowLayout.LEFT, 12, 2));
        options.add(pdfCheck);
        options.add(stepCheck);
        options.add(uppercaseCheck);
        c.gridx = 0; c.gridy = 3; c.gridwidth = 3; controls.add(options, c);
        c.gridx = 0; c.gridy = 4; c.gridwidth = 3;
        JPanel namingHelp = new JPanel(new FlowLayout(FlowLayout.LEFT, 5, 0));
        namingHelp.add(new JLabel("Use {MODEL.NAME} or {DRAWING.NAME}; unqualified names use the model first."));
        JButton parameters = new JButton("Available parameters...");
        parameters.addActionListener(new ActionListener() {
            public void actionPerformed(ActionEvent event) { showAvailableParameters(); }
        });
        JButton saveRules = new JButton("Save naming rules");
        saveRules.addActionListener(new ActionListener() {
            public void actionPerformed(ActionEvent event) {
                if (savePreferences()) JOptionPane.showMessageDialog(null,
                    "Naming rules and export settings saved for your Windows account.",
                    "Nexus Deliverables", JOptionPane.INFORMATION_MESSAGE);
                else JOptionPane.showMessageDialog(null,
                    "Could not save the naming rules. Check access to " + preferenceFile(),
                    "Nexus Deliverables", JOptionPane.ERROR_MESSAGE);
            }
        });
        namingHelp.add(parameters);
        namingHelp.add(saveRules);
        controls.add(namingHelp, c);
        root.add(controls, BorderLayout.NORTH);

        JTable table = new JTable(tableModel);
        drawingTable = table;
        table.setRowHeight(25);
        table.setFillsViewportHeight(true);
        table.setAutoCreateRowSorter(false);
        table.getColumnModel().getColumn(0).setMaxWidth(55);
        table.getColumnModel().getColumn(1).setPreferredWidth(185);
        table.getColumnModel().getColumn(2).setPreferredWidth(390);
        TableColumn itemColumn = table.getColumnModel().getColumn(3);
        itemColumn.setPreferredWidth(270);
        itemColumn.setCellRenderer(new TargetRenderer());
        itemColumn.setCellEditor(new TargetEditor());

        JPanel drawingPanel = new JPanel(new BorderLayout(5, 5));
        drawingPanel.setBorder(BorderFactory.createTitledBorder("Drawings already loaded in Creo"));
        JPanel selectButtons = new JPanel(new FlowLayout(FlowLayout.LEFT, 5, 0));
        JButton all = new JButton("Select all");
        all.addActionListener(new ActionListener() {
            public void actionPerformed(ActionEvent event) { setAllSelected(true); }
        });
        JButton none = new JButton("Select none");
        none.addActionListener(new ActionListener() {
            public void actionPerformed(ActionEvent event) { setAllSelected(false); }
        });
        selectButtons.add(all);
        selectButtons.add(none);
        selectButtons.add(countLabel);
        drawingPanel.add(selectButtons, BorderLayout.NORTH);
        drawingPanel.add(new JScrollPane(table), BorderLayout.CENTER);

        preview.setEditable(false);
        preview.setFont(new java.awt.Font("Monospaced", java.awt.Font.PLAIN, 12));
        JScrollPane previewScroll = new JScrollPane(preview);
        previewScroll.setBorder(BorderFactory.createTitledBorder("Export preview (existing files are never overwritten)"));
        previewScroll.setPreferredSize(new Dimension(1000, 235));
        JPanel center = new JPanel(new BorderLayout(6, 6));
        center.add(drawingPanel, BorderLayout.CENTER);
        center.add(previewScroll, BorderLayout.SOUTH);
        root.add(center, BorderLayout.CENTER);

        DocumentListener listener = new DocumentListener() {
            public void insertUpdate(DocumentEvent event) { refreshPreview(); }
            public void removeUpdate(DocumentEvent event) { refreshPreview(); }
            public void changedUpdate(DocumentEvent event) { refreshPreview(); }
        };
        pdfTemplateField.getDocument().addDocumentListener(listener);
        stepTemplateField.getDocument().addDocumentListener(listener);
        folderField.getDocument().addDocumentListener(listener);
        FocusAdapter templateFocus = new FocusAdapter() {
            public void focusGained(FocusEvent event) {
                activeTemplateField = (JTextField) event.getComponent();
            }
        };
        pdfTemplateField.addFocusListener(templateFocus);
        stepTemplateField.addFocusListener(templateFocus);
        ActionListener refreshListener = new ActionListener() {
            public void actionPerformed(ActionEvent event) { refreshPreview(); }
        };
        pdfCheck.addActionListener(refreshListener);
        stepCheck.addActionListener(refreshListener);
        uppercaseCheck.addActionListener(refreshListener);
        return root;
    }

    private List<DrawingRow> collectLoadedDrawings() throws jxthrowable {
        List<DrawingRow> loadedDrawings = new ArrayList<DrawingRow>();
        Models loaded = session.ListModelsByType(ModelType.MDL_DRAWING);
        for (int index = 0; loaded != null && index < loaded.getarraysize(); index++) {
            if (cancelled) return loadedDrawings;
            Model model = loaded.get(index);
            if (model == null) continue;
            DrawingRow row = new DrawingRow(model, logicalFileName(model));
            loadedDrawings.add(row);
        }
        return loadedDrawings;
    }

    private void requestDrawingDetails(final DrawingRow row) {
        if (!row.selected || row.detailsLoaded || row.loading) return;
        row.loading = true;
        row.loadError = "";
        creoWork.offer(new Runnable() {
            public void run() { loadDrawingDetails(row); }
        });
    }

    private void loadDrawingDetails(final DrawingRow row) {
        if (cancelled) return;
        final Map<String, String> drawingParameters = new LinkedHashMap<String, String>();
        final List<ModelInfo> references = new ArrayList<ModelInfo>();
        String error = "";
        boolean completed = false;
        if (row.selected) {
            try {
                drawingParameters.putAll(modelParameters(row.drawing));
                Models models = ((Model2D) row.drawing).ListModels();
                Set<String> names = new LinkedHashSet<String>();
                boolean interrupted = false;
                for (int index = 0; models != null && index < models.getarraysize(); index++) {
                    if (cancelled || !row.selected) { interrupted = true; break; }
                    Model model = models.get(index);
                    if (!isSolidModel(model)) continue;
                    String name = logicalFileName(model);
                    if (!names.add(name.toLowerCase(Locale.ROOT))) continue;
                    ModelInfo info = new ModelInfo(model, name);
                    info.parameters = modelParameters(model);
                    references.add(info);
                }
                completed = !interrupted && !cancelled && row.selected;
            } catch (Throwable failure) {
                error = failure.getMessage() == null ? String.valueOf(failure) : failure.getMessage();
            }
        }
        final String loadError = error;
        final boolean loadCompleted = completed;
        try {
            SwingUtilities.invokeAndWait(new Runnable() {
                public void run() {
                    row.loading = false;
                    if (!cancelled && row.selected) {
                        if (loadError.length() > 0) {
                            row.loadError = loadError;
                        } else if (loadCompleted) {
                            row.parameters = drawingParameters;
                            row.references.clear();
                            row.references.addAll(references);
                            row.namingModel = references.isEmpty() ? null : references.get(0);
                            row.detailsLoaded = true;
                        } else {
                            requestDrawingDetails(row);
                        }
                    }
                    if (!cancelled) {
                        tableModel.fireTableDataChanged();
                        updateLoadingState();
                        refreshPreview();
                    }
                }
            });
        } catch (Exception failure) {
            throw new IllegalStateException("Could not update the drawing list.", failure);
        }
    }

    private void updateLoadingState() {
        int selected = 0;
        int waiting = 0;
        int errors = 0;
        for (DrawingRow row : drawings) {
            if (!row.selected) continue;
            selected++;
            if (row.loading || (!row.detailsLoaded && row.loadError.length() == 0)) waiting++;
            if (row.loadError.length() > 0) errors++;
        }
        exportButton.setEnabled(selected > 0 && waiting == 0 && errors == 0);
        if (waiting > 0) loadingLabel.setText("Loading " + waiting + " selected drawing(s) from Creo...");
        else if (errors > 0) loadingLabel.setText("Could not load " + errors + " drawing(s). Uncheck and select again to retry.");
        else if (selected == 0) loadingLabel.setText("Select drawings to load their models and parameters.");
        else loadingLabel.setText(selected + " drawing(s) ready to export.");
    }

    private boolean validateSelections() {
        File folder = new File(folderField.getText().trim());
        if (!folder.isDirectory()) {
            JOptionPane.showMessageDialog(null, "Choose an existing output folder.",
                "Nexus Deliverables", JOptionPane.WARNING_MESSAGE);
            return false;
        }
        if (!pdfCheck.isSelected() && !stepCheck.isSelected()) {
            JOptionPane.showMessageDialog(null, "Select PDF, STEP, or both.",
                "Nexus Deliverables", JOptionPane.WARNING_MESSAGE);
            return false;
        }
        boolean anyDrawing = false;
        for (DrawingRow row : drawings) if (row.selected) anyDrawing = true;
        if (!anyDrawing) {
            JOptionPane.showMessageDialog(null, "Select at least one drawing.",
                "Nexus Deliverables", JOptionPane.WARNING_MESSAGE);
            return false;
        }
        List<ExportTask> tasks = buildPlan();
        int ready = 0;
        for (ExportTask task : tasks) {
            if (task.file.exists()) addReason(task, "File already exists; it will not be overwritten.");
            if (task.blockReason.length() == 0) ready++;
        }
        if (ready == 0) {
            JOptionPane.showMessageDialog(null,
                "No export is ready. Review existing files and duplicate names in the preview.",
                "Nexus Deliverables", JOptionPane.WARNING_MESSAGE);
            return false;
        }
        return true;
    }

    private List<ExportTask> buildPlan() {
        List<ExportTask> result = new ArrayList<ExportTask>();
        File folder = new File(folderField.getText().trim());
        Set<String> paths = new LinkedHashSet<String>();
        if (pdfCheck.isSelected()) {
            for (DrawingRow row : drawings) {
                if (!row.selected || !row.detailsLoaded) continue;
                ModelInfo namingModel = row.namingModel;
                ExportTask task = createTask("PDF", row.drawing, row.fileName,
                    pdfTemplateField.getText(), ".pdf", folder, namingModel, row);
                result.add(task);
            }
        }
        if (stepCheck.isSelected()) {
            LinkedHashMap<String, ModelInfo> unique = new LinkedHashMap<String, ModelInfo>();
            Map<String, DrawingRow> sourceDrawings = new LinkedHashMap<String, DrawingRow>();
            for (DrawingRow row : drawings) {
                if (!row.selected || !row.detailsLoaded) continue;
                for (ModelInfo info : row.references) {
                    String key = info.fileName.toLowerCase();
                    if (!unique.containsKey(key)) {
                        unique.put(key, info);
                        sourceDrawings.put(key, row);
                    }
                }
            }
            for (ModelInfo info : unique.values()) {
                ExportTask task = createTask("STEP", info.model, info.fileName,
                    stepTemplateField.getText(), ".step", folder, info,
                    sourceDrawings.get(info.fileName.toLowerCase()));
                task.drawingSource = sourceDrawings.get(info.fileName.toLowerCase()).fileName;
                task.modelInfo = info;
                result.add(task);
            }
        }

        for (ExportTask task : result) {
            String key = task.file.getAbsolutePath().toLowerCase();
            if (!paths.add(key)) addReason(task, "Duplicate output path in this batch.");
            if (task.file.getAbsolutePath().length() > 240) {
                addReason(task, "Output path is too long for reliable Creo/Windows export; choose a shorter folder or filename.");
            }
        }

        return result;
    }

    private ExportTask createTask(
        String kind, Model source, String sourceName, String template,
        String extension, File folder, ModelInfo namingModel, DrawingRow drawing
    ) {
        ExportTask task = new ExportTask();
        task.kind = kind;
        task.source = source;
        task.sourceName = sourceName;
        task.extension = extension;
        TemplateResult rendered = renderTemplate(
            template,
            namingModel == null ? new LinkedHashMap<String, String>() : namingModel.parameters,
            drawing == null ? new LinkedHashMap<String, String>() : drawing.parameters,
            extension, namingModel == null ? sourceName : namingModel.fileName
        );
        task.missing = rendered.missing;
        task.file = new File(folder, uppercaseCheck.isSelected()
            ? rendered.fileName.toUpperCase(Locale.ROOT) : rendered.fileName);
        return task;
    }

    private void refreshPreview() {
        if (drawings.isEmpty()) return;
        try {
            final List<ExportTask> tasks = buildPlan();
            final int generation = ++previewGeneration;
            preview.setText("Checking output filenames...\n");
            new SwingWorker<List<ExportTask>, Void>() {
                protected List<ExportTask> doInBackground() {
                    for (ExportTask task : tasks) {
                        if (task.file.exists()) addReason(task, "File already exists; it will not be overwritten.");
                    }
                    return tasks;
                }
                protected void done() {
                    if (generation != previewGeneration || cancelled
                        || setupDialog == null || !setupDialog.isDisplayable()) return;
                    try { renderPreview(get()); }
                    catch (Exception error) { preview.setText("Preview could not be generated: " + error.getMessage()); }
                }
            }.execute();
        } catch (Throwable error) {
            preview.setText("Preview could not be generated: " + error.getMessage());
        }
    }

    private void renderPreview(List<ExportTask> tasks) {
        try {
            int selectedDrawings = 0;
            for (DrawingRow row : drawings) if (row.selected) selectedDrawings++;
            countLabel.setText(selectedDrawings + " of " + drawings.size() + " drawings selected");
            preview.setText("");
            appendPreview("Selected drawings: " + selectedDrawings + " / " + drawings.size()
                + "   |   Green: ready   Orange: warning   Red: skipped\n", new Color(42, 67, 92), true);
            appendPreview("PDF: all sheets   |   STEP: each referenced model exported once\n\n", Color.DARK_GRAY, false);
            Map<String, ExportTask> pdfTasks = new LinkedHashMap<String, ExportTask>();
            Map<String, ExportTask> stepTasks = new LinkedHashMap<String, ExportTask>();
            for (ExportTask task : tasks) {
                ("PDF".equals(task.kind) ? pdfTasks : stepTasks)
                    .put(task.sourceName.toLowerCase(Locale.ROOT), task);
            }
            Set<String> shownSteps = new LinkedHashSet<String>();
            for (DrawingRow drawing : drawings) {
                if (!drawing.selected) continue;
                appendPreview("DRAWING  " + drawing.fileName + "\n", new Color(35, 78, 117), true);
                if (drawing.loading) appendPreview("  Loading models and parameters...\n", new Color(161, 91, 0), false);
                if (drawing.loadError.length() > 0) appendPreview("  ERROR - " + drawing.loadError + "\n", new Color(170, 35, 35), false);
                ExportTask pdf = pdfTasks.get(drawing.fileName.toLowerCase(Locale.ROOT));
                if (pdf != null) appendTaskPreview(pdf, false);
                for (ModelInfo model : drawing.references) {
                    String key = model.fileName.toLowerCase(Locale.ROOT);
                    ExportTask step = stepTasks.get(key);
                    if (step == null) continue;
                    appendTaskPreview(step, !shownSteps.add(key));
                }
                appendPreview("\n", Color.DARK_GRAY, false);
            }
            if (tasks.isEmpty() && selectedDrawings == 0) appendPreview("Select one or more drawings to load their details.\n", new Color(160, 95, 0), true);
            else if (tasks.isEmpty() && !pdfCheck.isSelected() && !stepCheck.isSelected()) appendPreview("Select at least one output format.\n", new Color(160, 95, 0), true);
            preview.setCaretPosition(0);
        } catch (Throwable error) {
            preview.setText("Preview could not be generated: " + error.getMessage());
        }
    }

    private void appendTaskPreview(ExportTask task, boolean shared) throws BadLocationException {
        Color status = task.blockReason.length() > 0 ? new Color(170, 35, 35) : new Color(29, 113, 68);
        appendPreview("  " + task.kind + "  " + task.sourceName + "  ->  " + task.file.getName()
            + (shared ? "  [shared; exported once]" : "") + "\n", status, true);
        if (task.blockReason.length() > 0)
            appendPreview("    ERROR - skipped: " + task.blockReason + "\n", new Color(170, 35, 35), false);
        if (!task.missing.isEmpty())
            appendPreview("    WARNING - omitted parameters: " + join(task.missing, ", ") + "\n", new Color(161, 91, 0), false);
        if (task.drawingSource.length() > 0)
            appendPreview("    Drawing parameters: " + task.drawingSource + "\n", Color.DARK_GRAY, false);
    }

    private void appendPreview(String text, Color color, boolean bold) throws BadLocationException {
        StyledDocument document = preview.getStyledDocument();
        SimpleAttributeSet style = new SimpleAttributeSet();
        StyleConstants.setForeground(style, color);
        StyleConstants.setBold(style, bold);
        document.insertString(document.getLength(), text, style);
    }

    private void execute(List<ExportTask> tasks) {
        final JDialog progress = createProgressDialog(tasks.size());
        int generated = 0;
        int skipped = 0;
        List<String> failures = new ArrayList<String>();
        for (int index = 0; index < tasks.size(); index++) {
            final ExportTask task = tasks.get(index);
            final int current = index + 1;
            if (task.blockReason.length() > 0) {
                skipped++;
                updateProgress(progress, current, tasks.size(), "Skipping " + task.file.getName() + ": " + task.blockReason);
                continue;
            }
            updateProgress(progress, current, tasks.size(), "Exporting " + task.kind + " " + task.sourceName);
            try {
                if (task.file.exists()) {
                    skipped++;
                    updateProgress(progress, current, tasks.size(), "Skipping existing file " + task.file.getName());
                    continue;
                }
                export(task);
                if (!task.file.isFile() || task.file.length() <= 0L) {
                    throw new IllegalStateException("Creo did not create a non-empty output file.");
                }
                generated++;
            } catch (Throwable error) {
                String message = error.getMessage() == null ? error.toString() : error.getMessage();
                failures.add(task.kind + " " + task.sourceName + ": " + message);
            }
        }
        SwingUtilities.invokeLater(new Runnable() {
            public void run() { progress.dispose(); }
        });
        StringBuilder summary = new StringBuilder();
        summary.append("Generated locally: ").append(generated).append('\n');
        summary.append("Skipped: ").append(skipped).append('\n');
        if (!failures.isEmpty()) {
            summary.append("Failed: ").append(failures.size()).append("\n\n");
            for (String failure : failures) summary.append("- ").append(failure).append('\n');
        }
        JOptionPane.showMessageDialog(null, summary.toString(),
            failures.isEmpty() ? "Nexus Deliverables Complete" : "Nexus Deliverables Finished with Issues",
            failures.isEmpty() ? JOptionPane.INFORMATION_MESSAGE : JOptionPane.WARNING_MESSAGE);
    }

    private void export(ExportTask task) throws Exception {
        String previousDirectory = session.GetCurrentDirectory();
        Window originalWindow = session.GetCurrentWindow();
        Window temporaryWindow = null;
        try {
            session.ChangeDirectory(task.file.getParent());
            if ("PDF".equals(task.kind)) {
                Window existing = null;
                try { existing = session.GetModelWindow(task.source); } catch (Throwable ignored) { }
                if (existing == null) {
                    temporaryWindow = session.CreateModelWindow(task.source);
                    task.source.Display();
                }
                PDFExportInstructions instructions = pfcExport.PDFExportInstructions_Create();
                instructions.SetFilePath(task.file.getAbsolutePath());
                PDFOptions options = PDFOptions.create();
                options.append(pdfOption(PDFOptionType.PDFOPT_SHEETS,
                    pfcArgument.CreateIntArgValue(PrintSheets.getValue(PrintSheets.PRINT_ALL_SHEETS))));
                options.append(pdfOption(PDFOptionType.PDFOPT_LAUNCH_VIEWER,
                    pfcArgument.CreateBoolArgValue(false)));
                instructions.SetOptions(options);
                task.source.Export(task.file.getName(), instructions);
            } else {
                GeomExportFlags flags = pfcModel.GeomExportFlags_Create();
                STEPExportInstructions instructions = pfcModel.STEPExportInstructions_Create(flags);
                task.source.Export(task.file.getName(), instructions);
            }
        } finally {
            if (temporaryWindow != null) {
                try { temporaryWindow.Close(); } catch (Throwable ignored) { }
            }
            if (originalWindow != null && originalWindow != temporaryWindow) {
                try { session.SetCurrentWindow(originalWindow); } catch (Throwable ignored) { }
            }
            if (previousDirectory != null && previousDirectory.length() > 0) {
                try { session.ChangeDirectory(previousDirectory); } catch (Throwable ignored) { }
            }
        }
    }

    private PDFOption pdfOption(PDFOptionType type, ArgValue value) throws jxthrowable {
        PDFOption option = pfcExport.PDFOption_Create();
        option.SetOptionType(type);
        option.SetOptionValue(value);
        return option;
    }

    private JDialog createProgressDialog(int total) {
        final JDialog dialog = new JDialog((Frame) null, "Exporting Creo Deliverables", false);
        final JLabel label = new JLabel("Preparing export...");
        final JProgressBar bar = new JProgressBar(0, Math.max(1, total));
        bar.setStringPainted(true);
        JPanel panel = new JPanel(new BorderLayout(8, 8));
        panel.setBorder(BorderFactory.createEmptyBorder(12, 14, 12, 14));
        panel.add(label, BorderLayout.NORTH);
        panel.add(bar, BorderLayout.CENTER);
        dialog.setContentPane(panel);
        dialog.setSize(520, 105);
        dialog.setLocationRelativeTo(null);
        dialog.setDefaultCloseOperation(JDialog.DO_NOTHING_ON_CLOSE);
        dialog.getRootPane().putClientProperty("progressLabel", label);
        dialog.getRootPane().putClientProperty("progressBar", bar);
        dialog.setVisible(true);
        return dialog;
    }

    private void updateProgress(final JDialog dialog, final int current, final int total, final String message) {
        SwingUtilities.invokeLater(new Runnable() {
            public void run() {
                JLabel label = (JLabel) dialog.getRootPane().getClientProperty("progressLabel");
                JProgressBar bar = (JProgressBar) dialog.getRootPane().getClientProperty("progressBar");
                if (label != null) label.setText(message);
                if (bar != null) { bar.setMaximum(Math.max(1, total)); bar.setValue(current); }
            }
        });
    }

    private void chooseFolder() {
        JFileChooser chooser = new JFileChooser(folderField.getText().trim());
        chooser.setDialogTitle("Choose deliverables output folder");
        chooser.setFileSelectionMode(JFileChooser.DIRECTORIES_ONLY);
        if (chooser.showOpenDialog(null) == JFileChooser.APPROVE_OPTION) {
            folderField.setText(chooser.getSelectedFile().getAbsolutePath());
            refreshPreview();
        }
    }

    private void setAllSelected(boolean selected) {
        for (DrawingRow drawing : drawings) {
            drawing.selected = selected;
            if (selected) requestDrawingDetails(drawing);
        }
        tableModel.fireTableDataChanged();
        updateLoadingState();
        refreshPreview();
    }

    private TemplateResult renderTemplate(String template, Map<String, String> modelValues,
        Map<String, String> drawingValues, String extension, String sourceName) {
        TemplateResult result = new TemplateResult();
        String source = (template == null ? "" : template.trim());
        if (source.toLowerCase().endsWith(extension)) source = source.substring(0, source.length() - extension.length());
        Matcher matcher = TOKEN.matcher(source);
        StringBuilder output = new StringBuilder();
        int previousEnd = 0;
        int tokenCount = 0;
        int resolvedCount = 0;
        boolean removeNextSeparator = false;
        while (matcher.find()) {
            tokenCount++;
            String literal = source.substring(previousEnd, matcher.start());
            if (removeNextSeparator) {
                String stripped = literal.replaceFirst("^[\\s._-]+", "");
                if (stripped.equals(literal)) trimTrailingSeparators(output);
                literal = stripped;
                removeNextSeparator = false;
            }
            output.append(literal);
            String token = matcher.group(1).trim();
            String value;
            if (token.regionMatches(true, 0, "DRAWING.", 0, 8)) {
                value = findParameter(drawingValues, token.substring(8));
            } else if (token.regionMatches(true, 0, "MODEL.", 0, 6)) {
                value = findParameter(modelValues, token.substring(6));
            } else {
                value = findParameter(modelValues, token);
                if (value.length() == 0) value = findParameter(drawingValues, token);
            }
            if (value.length() == 0) {
                if (!result.missing.contains(token)) result.missing.add(token);
                removeNextSeparator = true;
            } else {
                resolvedCount++;
                output.append(safeSegment(value));
            }
            previousEnd = matcher.end();
        }
        String tail = source.substring(previousEnd);
        if (removeNextSeparator) {
            String stripped = tail.replaceFirst("^[\\s._-]+", "");
            if (stripped.equals(tail) || stripped.length() == 0) trimTrailingSeparators(output);
            tail = stripped;
        }
        output.append(tail);
        String base = safeSegment(output.toString());
        while (base.endsWith(".") || base.endsWith(" ")) base = base.substring(0, base.length() - 1);
        if (base.length() == 0 || (tokenCount > 0 && resolvedCount == 0)) {
            base = safeSegment(sourceName.replaceFirst("(?i)\\.(prt|asm|drw)(?:\\.\\d+)?$", ""));
            if (base.length() == 0) base = "unnamed";
        }
        result.fileName = base + extension;
        return result;
    }

    private void trimTrailingSeparators(StringBuilder output) {
        while (output.length() > 0) {
            char ch = output.charAt(output.length() - 1);
            if (ch != '-' && ch != '_' && ch != '.' && !Character.isWhitespace(ch)) break;
            output.setLength(output.length() - 1);
        }
    }

    private void showAvailableParameters() {
        final JTextField target = activeTemplateField;
        final int selectionStart = target.getSelectionStart();
        final int selectionEnd = target.getSelectionEnd();
        DefaultListModel<ParameterChoice> choices = new DefaultListModel<ParameterChoice>();
        for (DrawingRow drawing : drawings) {
            if (!drawing.selected) continue;
            appendParameters(choices, "DRAWING", drawing.fileName, drawing.parameters);
            for (ModelInfo model : drawing.references) {
                appendParameters(choices, "MODEL", model.fileName, model.parameters);
            }
        }
        final JList<ParameterChoice> list = new JList<ParameterChoice>(choices);
        final JDialog dialog = new JDialog(SwingUtilities.getWindowAncestor(target),
            "Available Creo parameters", Dialog.ModalityType.APPLICATION_MODAL);
        list.addMouseListener(new MouseAdapter() {
            public void mouseClicked(MouseEvent event) {
                if (event.getClickCount() != 2 || !SwingUtilities.isLeftMouseButton(event)) return;
                int index = list.locationToIndex(event.getPoint());
                if (index < 0 || !list.getCellBounds(index, index).contains(event.getPoint())) return;
                ParameterChoice choice = list.getModel().getElementAt(index);
                int length = target.getDocument().getLength();
                target.select(Math.min(selectionStart, length), Math.min(selectionEnd, length));
                target.replaceSelection(choice.token);
                dialog.dispose();
                target.requestFocusInWindow();
            }
        });
        JPanel panel = new JPanel(new BorderLayout(8, 8));
        panel.setBorder(BorderFactory.createEmptyBorder(10, 10, 10, 10));
        String field = target == pdfTemplateField ? "PDF" : "STEP";
        panel.add(new JLabel("Double-click a parameter to insert it into the " + field + " filename rule."), BorderLayout.NORTH);
        panel.add(new JScrollPane(list), BorderLayout.CENTER);
        JButton close = new JButton("Close");
        close.addActionListener(new ActionListener() {
            public void actionPerformed(ActionEvent event) { dialog.dispose(); }
        });
        JPanel buttons = new JPanel(new FlowLayout(FlowLayout.RIGHT));
        buttons.add(close);
        panel.add(buttons, BorderLayout.SOUTH);
        dialog.setContentPane(panel);
        dialog.setSize(850, 500);
        dialog.setLocationRelativeTo(target);
        dialog.setVisible(true);
    }

    private void appendParameters(DefaultListModel<ParameterChoice> choices, String scope,
        String owner, Map<String, String> values) {
        for (Map.Entry<String, String> entry : values.entrySet()) {
            choices.addElement(new ParameterChoice("{" + scope + "." + entry.getKey() + "}",
                owner, entry.getValue()));
        }
    }

    private Map<String, String> modelParameters(Model model) throws jxthrowable {
        Map<String, String> result = new LinkedHashMap<String, String>();
        Parameters parameters = model.ListParams();
        for (int index = 0; parameters != null && index < parameters.getarraysize(); index++) {
            Parameter parameter = parameters.get(index);
            if (parameter == null) continue;
            try {
                String name = parameter.GetName();
                if (name == null || name.trim().length() == 0) continue;
                String text = parameterValue(parameter.GetValue());
                result.put(name.toUpperCase(), text == null ? "" : text.trim());
            } catch (Throwable ignored) {
                // A single unsupported Creo parameter must not hide the others.
            }
        }
        return result;
    }

    private String parameterValue(ParamValue value) throws jxthrowable {
        if (value == null) return "";
        ParamValueType type = value.Getdiscr();
        if (type == null) return "";
        int kind = type.getValue();
        if (kind == ParamValueType.PARAM_STRING.getValue()) return value.GetStringValue();
        if (kind == ParamValueType.PARAM_INTEGER.getValue()) return String.valueOf(value.GetIntValue());
        if (kind == ParamValueType.PARAM_DOUBLE.getValue()) return String.valueOf(value.GetDoubleValue());
        if (kind == ParamValueType.PARAM_BOOLEAN.getValue()) return String.valueOf(value.GetBoolValue());
        return "";
    }

    private String findParameter(Map<String, String> values, String name) {
        String value = values.get(name.toUpperCase());
        return value == null ? "" : value.trim();
    }

    private String safeSegment(String value) {
        String clean = value == null ? "" : value.trim();
        clean = clean.replaceAll("[<>:\"/\\\\|?*\\p{Cntrl}]", "_");
        return clean.replaceAll("\\s+", " ");
    }

    private boolean isSolidModel(Model model) throws jxthrowable {
        return model != null && (isType(model, ModelType.MDL_PART) || isType(model, ModelType.MDL_ASSEMBLY));
    }

    private boolean isType(Model model, ModelType type) throws jxthrowable {
        return model.GetType() != null && type != null
            && model.GetType().getValue() == type.getValue();
    }

    private String logicalFileName(Model model) throws jxthrowable {
        String name = new File(model.GetFileName()).getName();
        return name.replaceFirst("(?i)\\.(prt|asm|drw)\\.\\d+$", ".$1")
            .replaceFirst("(?i)^([^<>]+)<[^<>]+>(\\.(?:prt|asm|drw))$", "$1$2");
    }

    private String join(List<String> values, String separator) {
        StringBuilder text = new StringBuilder();
        for (String value : values) {
            if (text.length() > 0) text.append(separator);
            text.append(value);
        }
        return text.toString();
    }

    private void addReason(ExportTask task, String reason) {
        if (task.blockReason.length() > 0) task.blockReason += "; ";
        task.blockReason += reason;
    }

    private File preferenceFile() {
        String local = System.getenv("LOCALAPPDATA");
        if (local == null || local.trim().length() == 0) {
            local = System.getProperty("user.home") + File.separator + "AppData" + File.separator + "Local";
        }
        return new File(new File(new File(local, "CreoVCS"), "jlink"), "deliverables.properties");
    }

    private void loadPreferences() {
        File file = preferenceFile();
        if (!file.isFile()) return;
        FileInputStream input = null;
        try { input = new FileInputStream(file); preferences.load(input); }
        catch (Exception ignored) { }
        finally { if (input != null) try { input.close(); } catch (Exception ignored) { } }
    }

    private boolean savePreferences() {
        preferences.setProperty("outputFolder", folderField.getText().trim());
        preferences.setProperty("pdfTemplate", pdfTemplateField.getText().trim());
        preferences.setProperty("stepTemplate", stepTemplateField.getText().trim());
        preferences.setProperty("includePdf", String.valueOf(pdfCheck.isSelected()));
        preferences.setProperty("includeStep", String.valueOf(stepCheck.isSelected()));
        preferences.setProperty("uppercaseNames", String.valueOf(uppercaseCheck.isSelected()));
        File file = preferenceFile();
        File directory = file.getParentFile();
        if (!directory.exists()) directory.mkdirs();
        FileOutputStream output = null;
        try { output = new FileOutputStream(file); preferences.store(output, "Nexus Creo Deliverable Exporter"); return true; }
        catch (Exception ignored) { return false; }
        finally { if (output != null) try { output.close(); } catch (Exception ignored) { } }
    }

    private void updateRow(DrawingRow row, int column, Object value) {
        if (column == 0 && value instanceof Boolean) {
            row.selected = ((Boolean) value).booleanValue();
            if (row.selected) requestDrawingDetails(row);
        }
        if (column == 3 && value instanceof ModelInfo) row.namingModel = (ModelInfo) value;
        updateLoadingState();
        refreshPreview();
    }

    private final class ExportTableModel extends AbstractTableModel {
        private static final long serialVersionUID = 1L;
        public int getRowCount() { return drawings.size(); }
        public int getColumnCount() { return COLUMNS.length; }
        public String getColumnName(int column) { return COLUMNS[column]; }
        public Class<?> getColumnClass(int column) { return column == 0 ? Boolean.class : Object.class; }
        public boolean isCellEditable(int row, int column) { return column == 0 || (column == 3 && drawings.get(row).detailsLoaded); }
        public Object getValueAt(int index, int column) {
            DrawingRow row = drawings.get(index);
            if (column == 0) return Boolean.valueOf(row.selected);
            if (column == 1) return row.fileName;
            if (column == 2) {
                if (row.loading) return "Loading...";
                if (row.loadError.length() > 0) return "Could not load";
                if (!row.detailsLoaded) return "Select drawing to load";
                List<String> names = new ArrayList<String>();
                for (ModelInfo info : row.references) names.add(info.fileName);
                return join(names, ", ");
            }
            return row.namingModel == null ? (row.detailsLoaded ? "No referenced model" : "Not loaded") : row.namingModel;
        }
        public void setValueAt(Object value, int row, int column) {
            updateRow(drawings.get(row), column, value);
            fireTableRowsUpdated(row, row);
        }
    }

    private final class TargetRenderer extends DefaultTableCellRenderer {
        private static final long serialVersionUID = 1L;
        public java.awt.Component getTableCellRendererComponent(
            JTable table, Object value, boolean selected, boolean focused, int row, int column
        ) {
            return super.getTableCellRendererComponent(table, value, selected, focused, row, column);
        }
    }

    private final class TargetEditor extends AbstractCellEditor implements TableCellEditor {
        private static final long serialVersionUID = 1L;
        private final JComboBox<ModelInfo> combo = new JComboBox<ModelInfo>();
        public java.awt.Component getTableCellEditorComponent(
            JTable table, Object value, boolean selected, int row, int column
        ) {
            combo.removeAllItems();
            DrawingRow drawing = drawings.get(row);
            for (ModelInfo model : drawing.references) combo.addItem(model);
            if (value instanceof ModelInfo) combo.setSelectedItem(value);
            return combo;
        }
        public Object getCellEditorValue() { return combo.getSelectedItem(); }
    }

    private static final class DrawingRow {
        final Model drawing;
        final String fileName;
        final List<ModelInfo> references = new ArrayList<ModelInfo>();
        Map<String, String> parameters = new LinkedHashMap<String, String>();
        volatile boolean selected;
        boolean loading;
        boolean detailsLoaded;
        String loadError = "";
        ModelInfo namingModel;
        DrawingRow(Model drawing, String fileName) { this.drawing = drawing; this.fileName = fileName; }
    }

    private static final class ModelInfo {
        final Model model;
        final String fileName;
        Map<String, String> parameters = new LinkedHashMap<String, String>();
        ModelInfo(Model model, String fileName) { this.model = model; this.fileName = fileName; }
        public String toString() { return fileName; }
    }

    private static final class ExportTask {
        String kind;
        String sourceName;
        String extension;
        Model source;
        ModelInfo modelInfo;
        File file;
        List<String> missing = new ArrayList<String>();
        String blockReason = "";
        String drawingSource = "";
    }

    private static final class TemplateResult {
        String fileName;
        List<String> missing = new ArrayList<String>();
    }

    private static final class ParameterChoice {
        final String token;
        final String owner;
        final String value;
        ParameterChoice(String token, String owner, String value) {
            this.token = token; this.owner = owner; this.value = value;
        }
        public String toString() {
            return token + "   |   " + owner + "   |   " + (value.length() == 0 ? "(empty)" : value);
        }
    }
}
