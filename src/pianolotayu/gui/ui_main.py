# -*- coding: utf-8 -*-

################################################################################
## Form generated from reading UI file 'main.ui'
##
## Created by: Qt User Interface Compiler version 6.11.1
##
## WARNING! All changes made in this file will be lost when recompiling UI file!
################################################################################

from PySide6.QtCore import (QCoreApplication, QDate, QDateTime, QLocale,
    QMetaObject, QObject, QPoint, QRect,
    QSize, QTime, QUrl, Qt)
from PySide6.QtGui import (QBrush, QColor, QConicalGradient, QCursor,
    QFont, QFontDatabase, QGradient, QIcon,
    QImage, QKeySequence, QLinearGradient, QPainter,
    QPalette, QPixmap, QRadialGradient, QTransform)
from PySide6.QtWidgets import (QApplication, QCheckBox, QDoubleSpinBox, QFormLayout,
    QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QProgressBar, QPushButton, QSizePolicy, QSpinBox,
    QVBoxLayout, QWidget)

class Ui_Form(object):
    def setupUi(self, Form):
        if not Form.objectName():
            Form.setObjectName(u"Form")
        Form.resize(480, 490)
        Form.setMinimumSize(QSize(480, 490))
        self.gridLayout = QGridLayout(Form)
        self.gridLayout.setObjectName(u"gridLayout")
        self.horizontalLayout_4 = QHBoxLayout()
        self.horizontalLayout_4.setObjectName(u"horizontalLayout_4")
        self.titleLabel = QLabel(Form)
        self.titleLabel.setObjectName(u"titleLabel")
        sizePolicy = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Preferred)
        sizePolicy.setHorizontalStretch(0)
        sizePolicy.setVerticalStretch(0)
        sizePolicy.setHeightForWidth(self.titleLabel.sizePolicy().hasHeightForWidth())
        self.titleLabel.setSizePolicy(sizePolicy)
        font = QFont()
        font.setPointSize(18)
        font.setBold(False)
        font.setItalic(False)
        self.titleLabel.setFont(font)

        self.horizontalLayout_4.addWidget(self.titleLabel)

        self.versionLabel = QLabel(Form)
        self.versionLabel.setObjectName(u"versionLabel")
        self.versionLabel.setMinimumSize(QSize(0, 25))
        self.versionLabel.setMaximumSize(QSize(16777215, 25))
        self.versionLabel.setAlignment(Qt.AlignmentFlag.AlignBottom|Qt.AlignmentFlag.AlignLeading|Qt.AlignmentFlag.AlignLeft)

        self.horizontalLayout_4.addWidget(self.versionLabel)


        self.gridLayout.addLayout(self.horizontalLayout_4, 0, 0, 1, 3)

        self.progressBar = QProgressBar(Form)
        self.progressBar.setObjectName(u"progressBar")
        self.progressBar.setValue(0)
        self.progressBar.setTextVisible(False)

        self.gridLayout.addWidget(self.progressBar, 2, 0, 1, 3)

        self.verticalLayout = QVBoxLayout()
        self.verticalLayout.setObjectName(u"verticalLayout")
        self.horizontalLayout = QHBoxLayout()
        self.horizontalLayout.setObjectName(u"horizontalLayout")
        self.filePathEdit = QLineEdit(Form)
        self.filePathEdit.setObjectName(u"filePathEdit")
        self.filePathEdit.setMinimumSize(QSize(0, 0))
        self.filePathEdit.setMaximumSize(QSize(16777215, 16777215))

        self.horizontalLayout.addWidget(self.filePathEdit)


        self.verticalLayout.addLayout(self.horizontalLayout)

        self.outputLabel = QLabel(Form)
        self.outputLabel.setObjectName(u"outputLabel")

        self.verticalLayout.addWidget(self.outputLabel)

        self.addFileArea = QPushButton(Form)
        self.addFileArea.setObjectName(u"addFileArea")
        sizePolicy1 = QSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)
        sizePolicy1.setHorizontalStretch(0)
        sizePolicy1.setVerticalStretch(0)
        sizePolicy1.setHeightForWidth(self.addFileArea.sizePolicy().hasHeightForWidth())
        self.addFileArea.setSizePolicy(sizePolicy1)
        font1 = QFont()
        font1.setPointSize(36)
        self.addFileArea.setFont(font1)

        self.verticalLayout.addWidget(self.addFileArea)


        self.gridLayout.addLayout(self.verticalLayout, 1, 2, 1, 1)

        self.gridLayout_2 = QGridLayout()
        self.gridLayout_2.setObjectName(u"gridLayout_2")
        self.convertButton = QPushButton(Form)
        self.convertButton.setObjectName(u"convertButton")

        self.gridLayout_2.addWidget(self.convertButton, 0, 0, 1, 1)

        self.previewButton = QPushButton(Form)
        self.previewButton.setObjectName(u"previewButton")

        self.gridLayout_2.addWidget(self.previewButton, 0, 1, 1, 1)

        self.formLayout = QFormLayout()
        self.formLayout.setObjectName(u"formLayout")
        self.label_3 = QLabel(Form)
        self.label_3.setObjectName(u"label_3")

        self.formLayout.setWidget(0, QFormLayout.ItemRole.LabelRole, self.label_3)

        self.sampleRateBox = QSpinBox(Form)
        self.sampleRateBox.setObjectName(u"sampleRateBox")
        sizePolicy2 = QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        sizePolicy2.setHorizontalStretch(0)
        sizePolicy2.setVerticalStretch(0)
        sizePolicy2.setHeightForWidth(self.sampleRateBox.sizePolicy().hasHeightForWidth())
        self.sampleRateBox.setSizePolicy(sizePolicy2)
        self.sampleRateBox.setMaximum(999999)
        self.sampleRateBox.setSingleStep(1000)

        self.formLayout.setWidget(0, QFormLayout.ItemRole.FieldRole, self.sampleRateBox)

        self.label_4 = QLabel(Form)
        self.label_4.setObjectName(u"label_4")

        self.formLayout.setWidget(1, QFormLayout.ItemRole.LabelRole, self.label_4)

        self.windowFFTBox = QSpinBox(Form)
        self.windowFFTBox.setObjectName(u"windowFFTBox")
        sizePolicy2.setHeightForWidth(self.windowFFTBox.sizePolicy().hasHeightForWidth())
        self.windowFFTBox.setSizePolicy(sizePolicy2)
        self.windowFFTBox.setMaximum(999999)
        self.windowFFTBox.setSingleStep(256)

        self.formLayout.setWidget(1, QFormLayout.ItemRole.FieldRole, self.windowFFTBox)

        self.label_5 = QLabel(Form)
        self.label_5.setObjectName(u"label_5")

        self.formLayout.setWidget(2, QFormLayout.ItemRole.LabelRole, self.label_5)

        self.hopLengthBox = QSpinBox(Form)
        self.hopLengthBox.setObjectName(u"hopLengthBox")
        sizePolicy2.setHeightForWidth(self.hopLengthBox.sizePolicy().hasHeightForWidth())
        self.hopLengthBox.setSizePolicy(sizePolicy2)
        self.hopLengthBox.setMaximum(999999)
        self.hopLengthBox.setSingleStep(32)

        self.formLayout.setWidget(2, QFormLayout.ItemRole.FieldRole, self.hopLengthBox)

        self.label_6 = QLabel(Form)
        self.label_6.setObjectName(u"label_6")

        self.formLayout.setWidget(3, QFormLayout.ItemRole.LabelRole, self.label_6)

        self.thresholdBox = QDoubleSpinBox(Form)
        self.thresholdBox.setObjectName(u"thresholdBox")
        sizePolicy2.setHeightForWidth(self.thresholdBox.sizePolicy().hasHeightForWidth())
        self.thresholdBox.setSizePolicy(sizePolicy2)
        self.thresholdBox.setMaximum(999.000000000000000)
        self.thresholdBox.setSingleStep(0.500000000000000)

        self.formLayout.setWidget(3, QFormLayout.ItemRole.FieldRole, self.thresholdBox)

        self.label_7 = QLabel(Form)
        self.label_7.setObjectName(u"label_7")

        self.formLayout.setWidget(4, QFormLayout.ItemRole.LabelRole, self.label_7)

        self.maxFrameNotesBox = QSpinBox(Form)
        self.maxFrameNotesBox.setObjectName(u"maxFrameNotesBox")
        sizePolicy2.setHeightForWidth(self.maxFrameNotesBox.sizePolicy().hasHeightForWidth())
        self.maxFrameNotesBox.setSizePolicy(sizePolicy2)
        self.maxFrameNotesBox.setMaximum(999)

        self.formLayout.setWidget(4, QFormLayout.ItemRole.FieldRole, self.maxFrameNotesBox)

        self.label_8 = QLabel(Form)
        self.label_8.setObjectName(u"label_8")

        self.formLayout.setWidget(5, QFormLayout.ItemRole.LabelRole, self.label_8)

        self.minDurationBox = QSpinBox(Form)
        self.minDurationBox.setObjectName(u"minDurationBox")
        sizePolicy2.setHeightForWidth(self.minDurationBox.sizePolicy().hasHeightForWidth())
        self.minDurationBox.setSizePolicy(sizePolicy2)
        self.minDurationBox.setMaximum(999999)

        self.formLayout.setWidget(5, QFormLayout.ItemRole.FieldRole, self.minDurationBox)

        self.label_9 = QLabel(Form)
        self.label_9.setObjectName(u"label_9")

        self.formLayout.setWidget(6, QFormLayout.ItemRole.LabelRole, self.label_9)

        self.dynamicRangeBox = QDoubleSpinBox(Form)
        self.dynamicRangeBox.setObjectName(u"dynamicRangeBox")
        sizePolicy2.setHeightForWidth(self.dynamicRangeBox.sizePolicy().hasHeightForWidth())
        self.dynamicRangeBox.setSizePolicy(sizePolicy2)
        self.dynamicRangeBox.setMaximum(999.000000000000000)
        self.dynamicRangeBox.setSingleStep(0.500000000000000)

        self.formLayout.setWidget(6, QFormLayout.ItemRole.FieldRole, self.dynamicRangeBox)

        self.label_12 = QLabel(Form)
        self.label_12.setObjectName(u"label_12")

        self.formLayout.setWidget(7, QFormLayout.ItemRole.LabelRole, self.label_12)

        self.highDampBox = QDoubleSpinBox(Form)
        self.highDampBox.setObjectName(u"highDampBox")
        sizePolicy2.setHeightForWidth(self.highDampBox.sizePolicy().hasHeightForWidth())
        self.highDampBox.setSizePolicy(sizePolicy2)
        self.highDampBox.setMaximum(10.000000000000000)
        self.highDampBox.setSingleStep(0.050000000000000)

        self.formLayout.setWidget(7, QFormLayout.ItemRole.FieldRole, self.highDampBox)

        self.label_13 = QLabel(Form)
        self.label_13.setObjectName(u"label_13")

        self.formLayout.setWidget(8, QFormLayout.ItemRole.LabelRole, self.label_13)

        self.voiceBoostBox = QDoubleSpinBox(Form)
        self.voiceBoostBox.setObjectName(u"voiceBoostBox")
        sizePolicy2.setHeightForWidth(self.voiceBoostBox.sizePolicy().hasHeightForWidth())
        self.voiceBoostBox.setSizePolicy(sizePolicy2)
        self.voiceBoostBox.setMaximum(10.000000000000000)
        self.voiceBoostBox.setSingleStep(0.050000000000000)

        self.formLayout.setWidget(8, QFormLayout.ItemRole.FieldRole, self.voiceBoostBox)

        self.label_10 = QLabel(Form)
        self.label_10.setObjectName(u"label_10")

        self.formLayout.setWidget(9, QFormLayout.ItemRole.LabelRole, self.label_10)

        self.pianoLimitSwitch = QCheckBox(Form)
        self.pianoLimitSwitch.setObjectName(u"pianoLimitSwitch")
        sizePolicy3 = QSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        sizePolicy3.setHorizontalStretch(0)
        sizePolicy3.setVerticalStretch(0)
        sizePolicy3.setHeightForWidth(self.pianoLimitSwitch.sizePolicy().hasHeightForWidth())
        self.pianoLimitSwitch.setSizePolicy(sizePolicy3)
        self.pianoLimitSwitch.setChecked(False)

        self.formLayout.setWidget(9, QFormLayout.ItemRole.FieldRole, self.pianoLimitSwitch)


        self.gridLayout_2.addLayout(self.formLayout, 1, 0, 1, 2)


        self.gridLayout.addLayout(self.gridLayout_2, 1, 0, 1, 1)


        self.retranslateUi(Form)

        QMetaObject.connectSlotsByName(Form)
    # setupUi

    def retranslateUi(self, Form):
        Form.setWindowTitle(QCoreApplication.translate("Form", u"PianoLoTayu", None))
        self.titleLabel.setText(QCoreApplication.translate("Form", u"PianoLoTayu", None))
        self.versionLabel.setText(QCoreApplication.translate("Form", u"(version)", None))
        self.filePathEdit.setPlaceholderText(QCoreApplication.translate("Form", u"\u8f93\u5165\u6587\u4ef6\u8def\u5f84...", None))
        self.outputLabel.setText(QCoreApplication.translate("Form", u"\u8f93\u51fa\u8def\u5f84\uff1a\uff08\u8bf7\u6253\u5f00\u6587\u4ef6\uff09", None))
        self.addFileArea.setText(QCoreApplication.translate("Form", u"+", None))
        self.convertButton.setText(QCoreApplication.translate("Form", u"\u5f00\u59cb\u8f6c\u6362", None))
        self.previewButton.setText(QCoreApplication.translate("Form", u"\u9884\u89c8", None))
        self.label_3.setText(QCoreApplication.translate("Form", u"\u5206\u6790\u91c7\u6837\u7387(Hz)", None))
        self.label_4.setText(QCoreApplication.translate("Form", u"FFT\u7a97\u53e3\u5927\u5c0f", None))
        self.label_5.setText(QCoreApplication.translate("Form", u"STFT\u5e27\u95f4\u8df3\u91c7\u6570", None))
        self.label_6.setText(QCoreApplication.translate("Form", u"\u5cf0\u503c\u9608\u503c(dB)", None))
        self.label_7.setText(QCoreApplication.translate("Form", u"\u6bcf\u5e27\u6700\u591a\u97f3\u7b26\u6570", None))
        self.label_8.setText(QCoreApplication.translate("Form", u"\u6700\u77ed\u97f3\u7b26\u65f6\u957f(ms)", None))
        self.label_9.setText(QCoreApplication.translate("Form", u"\u52a8\u6001\u8303\u56f4(dB)", None))
        self.label_12.setText(QCoreApplication.translate("Form", u"\u9ad8\u9891\u8870\u51cf(0~2)", None))
        self.label_13.setText(QCoreApplication.translate("Form", u"\u4eba\u58f0\u589e\u5f3a(0~2)", None))
        self.label_10.setText(QCoreApplication.translate("Form", u"\u5173\u95ed\u94a2\u7434\u97f3\u57df\u6298\u53e0", None))
        self.pianoLimitSwitch.setText("")
    # retranslateUi

