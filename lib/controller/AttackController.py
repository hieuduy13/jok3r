#!/usr/bin/env python3
# -*- coding: utf-8 -*-
###
### Core > Attack Controller
###
import datetime
from collections import defaultdict

from lib.core.AttackScope import AttackScope
from lib.core.Constants import *
from lib.core.Exceptions import AttackException, TargetException
from lib.core.Target import Target
from lib.controller.Controller import Controller
from lib.db.Credential import Credential
from lib.db.Host import Host
from lib.db.Mission import Mission
from lib.db.Option import Option
from lib.db.Service import Service, Protocol
from lib.output.Logger import logger


class AttackController(Controller):

    def run(self):
        """Run the Attack Controller"""

        args = self.arguments.args

        # Context parameters are organized in dict 
        # { service : list of db objects }
        self.creds    = defaultdict(list)
        self.users    = defaultdict(list)
        self.products = defaultdict(list)
        self.options  = defaultdict(list)

        if args.creds:
            for c in args.creds:
                self.creds[c['service']].append(
                    Credential(type=c['auth_type'], 
                               username=c['username'], 
                               password=c['password']))
        if args.users:
            for u in args.users:
                self.users[c['service']].append(
                    Credential(type=u['auth_type'], 
                               username=u['username'], 
                               password=None))

        if args.products:
            for type_,name in args.products.items():
                service = self.settings.services.get_service_for_product_type(type_)
                if service:
                    self.products[service].append(
                        Product(type=type_,
                                name=name))

        if args.options:
            for name, value in args.options.items():
                service = self.settings.services.get_service_for_specific_option(name)
                if service:
                    self.options[service].append(
                        Option(name=name, 
                               value=value))

        # Attack configuration
        categories = self.settings.services.list_all_categories() # default: all

        if args.cat_only:
            categories = [ cat for cat in categories if cat in args.cat_only ]
        elif args.cat_exclude:
            categories = [ cat for cat in categories if cat not in args.cat_exclude ]

        # Run the attack
        self.attack_scope = AttackScope(self.settings, 
                                        self.arguments,
                                        self.sqlsess,
                                        filter_categories=categories, 
                                        filter_checks=args.checks, 
                                        attack_profile=args.profile,
                                        fast_mode=args.fast_mode)

        begin = datetime.datetime.now()
        if args.target_ip_or_url:
            self.__run_for_single_target(args)
        else:
            self.__run_for_multi_targets(args)
            
        print()
        duration = datetime.datetime.now() - begin
        logger.info('Done. Time spent: {} seconds'.format(duration.seconds))


    #------------------------------------------------------------------------------------
    # Single-Target mode

    def __run_for_single_target(self, args):
        """Run attack against a single target specified into args"""
        
        req = ServicesRequester(self.sqlsess)
        mission = None

        # Get Mission if target must be added into a mission scope
        if args.add:
            mission = self.sqlsess.query(Mission).filter(Mission.name == args.add).first()
            if not mission:
                raise AttackException('The specified mission does not exist in the ' \
                    'database. You should create it if needed')

        # Create new Service/Host objects (if service already exist, 
        # will be merged by ServicesRequester.add_target)
        url = args.target_ip_or_url if args.target_mode == TargetMode.URL else ''
        ip  = args.target_ip_or_url if args.target_mode == TargetMode.IP else ''
        service = Service(name=args.service,
                          port=int(args.target_port),
                          protocol=self.settings.services.get_protocol2(args.service),
                          url=url)
        host = Host(ip=ip) # Will be updated when initializing Target()
        host.services.append(service)

        # Update credentials and options if needed
        for c in self.creds[args.service]   : service.credentials.append(c)
        for u in self.users[args.service]   : service.credentials.append(u)
        for o in self.options[args.service] : service.options.append(o)

        # Initialize Target and check if reachable 
        # (optionally perform reverve DNS lookup & Nmap banner grabbing)
        try:
            target = Target(service, self.settings.services)
        except TargetException as e:
            logger.error(e)
            sys.exit(1)

        # Commit new data into database if target must be added to a mission
        if mission:
            logger.info('Results from this attack will be saved under mission ' \
                '"{mission}" in database'.format(mission=mission.name))
            req.select_mission(mission.name)
            req.add_target(target)

        # Run the attack
        self.attack_scope.add_target(target)
        self.attack_scope.attack()


    #------------------------------------------------------------------------------------
    # Multi-Targets mode

    def __run_for_multi_targets(self, args):
        """Run attack against multiple targets from the database"""

        # Get Mission from which targets must be extracted
        mission = self.sqlsess.query(Mission)\
                    .filter(Mission.name == args.mission).first()
        if mission:
            logger.info('Extracting targets from mission "{mission}" ...'.format(
                mission=mission.name))
        else:
            raise AttackException('Mission {mission} does not exist into the ' \
                'database'.format(mission=args.mission))

        # Initialize Services requester and add filter if provided
        req = ServicesRequester(self.sqlsess)
        req.select_mission(args.mission)

        if args.filters_combined:
            for filt in args.filter:
                logger.info('Applying filters on mission scope: {filter}'.format(
                    filter=filt))
            if len(args.filter) > 1:
                logger.info('Note: Logical OR is applied between each filter')
            req.add_filter(args.filters_combined)

        # Retrieve targeted services from database
        services = req.get_results()
        if not services:
            raise AttackException('There is no matching service to target into the ' \
                'database')

        # Add each targeted service into Attack scope 
        for service in services:

            # Update credentials and options if needed
            for c in self.creds[service.name]   : service.credentials.append(c)
            for u in self.users[service.name]   : service.credentials.append(u)
            for o in self.options[service.name] : service.options.append(o)

            # Initialize Target 
            try:
                target = Target(service, self.settings.services)
            except TargetException as e:
                logger.error(e)
                continue

            self.attack_scope.add_target(target)

        # Run the attack
        self.attack_scope.attack()